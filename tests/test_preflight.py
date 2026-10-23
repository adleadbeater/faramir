"""Increment 12: preflight fails closed on every configuration and schema
mismatch, before any operational write, and never repairs anything."""

import json

import pytest

from faramir import preflight as P
from faramir.sheet import LEGACY_STATE_HEADERS, STATE_HEADERS
from tests.fakes.fake_anthropic import FakeAnthropic
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fakes.state_book import init_book, state_row
from tests.test_run_daily_corpus import V2_PATH, env  # noqa: F401
from tests.test_run_daily_runtime import artifacts, main

LIVE_ENV = {"FARAMIR_ENABLED": "true", **{k: "x" for k in P.LIVE_SETTINGS}}


def workbook(collect=False):
    gc = FakeClient()
    b = gc.book("S")
    init_book(b, [state_row(1, "One")])
    b.add_tab("suggestions")
    if collect:
        from faramir.history import COLLECTION_HEADERS, COLLECTIONS_TAB, HISTORY_HEADERS, HISTORY_TAB

        b.add_tab(HISTORY_TAB, [HISTORY_HEADERS])
        b.add_tab(COLLECTIONS_TAB, [COLLECTION_HEADERS])
    return gc


# ── settings ────────────────────────────────────────────────────────────

def test_live_settings_complete():
    assert P.check_settings(live=True, env=LIVE_ENV).ok


@pytest.mark.parametrize("value", [None, "", "false", "TRUE ", "yes", "1"])
def test_unset_or_non_true_enable_means_disabled(value):
    e = dict(LIVE_ENV)
    if value is None:
        e.pop("FARAMIR_ENABLED")
    else:
        e["FARAMIR_ENABLED"] = value
    ok = P.check_settings(live=True, env=e).ok
    assert ok is (value == "TRUE ")  # case/space-insensitive "true" only


@pytest.mark.parametrize("missing", P.LIVE_SETTINGS)
def test_each_live_setting_is_required(missing):
    e = {k: v for k, v in LIVE_ENV.items() if k != missing}
    p = P.check_settings(live=True, env=e)
    assert not p.ok and p.problems == [f"missing required setting {missing}"]


def test_dry_run_needs_neither_enable_nor_keys():
    assert P.check_settings(live=False, env={"FARAMIR_SHEET_ID": "S", "SLACK_WEBHOOK_URL": "w"}).ok


# ── Sheets schema ───────────────────────────────────────────────────────

def test_migrated_workbook_passes():
    assert P.check_sheets(workbook(), "S").ok
    assert P.check_sheets(workbook(collect=True), "S", collect_history=True).ok


def _problems(gc, **kw):
    p = P.check_sheets(gc, "S", **kw)
    assert not p.ok
    return " | ".join(p.problems)


@pytest.mark.parametrize("mutate, expect", [
    (lambda b: b._sheets.pop("faramir_meta"), "schema version is None"),
    (lambda b: b.worksheet("faramir_meta").rows.__setitem__(1, ["schema_version", "1", "", ""]), "schema version is '1'"),
    (lambda b: b.worksheet("faramir_meta").rows.__setitem__(0, ["k", "v"]), "schema version is 'unreadable'"),
    (lambda b: b._sheets.pop("state_meta"), "required tab 'state_meta' is missing"),
    (lambda b: b._sheets.pop("state_lkg"), "required tab 'state_lkg' is missing"),
    (lambda b: b._sheets.pop("Usage"), "required tab 'Usage' is missing"),
    (lambda b: b._sheets.pop("suggestions"), "required tab 'suggestions' is missing"),
    (lambda b: b.worksheet("state").rows.__setitem__(0, list(LEGACY_STATE_HEADERS)), "legacy 17-column layout"),
    (lambda b: b.worksheet("state").rows.__setitem__(0, list(reversed(STATE_HEADERS))), "state header does not match"),
    (lambda b: b.worksheet("Usage").rows.__setitem__(0, ["ts"]), "Usage header does not match"),
    (lambda b: b.worksheet("state_meta").rows.__setitem__(0, ["key"]), "state_meta header does not match"),
    (lambda b: b.worksheet("suggestions").rows.extend([["run_date", "x"], ["2026-10-01", "y"]]), "suggestions"),
    (lambda b: b.add_tab("identity_overrides", [["slug", "id"]]), "identity_overrides lacks"),
])
def test_each_schema_mismatch_fails(mutate, expect):
    gc = workbook()
    mutate(gc.book("S"))
    assert expect in _problems(gc)


def test_history_tabs_required_only_when_collection_enabled():
    gc = workbook()
    assert P.check_sheets(gc, "S").ok
    assert "required tab 'weekend_history' is missing" in _problems(gc, collect_history=True)


def test_read_error_fails_closed():
    gc = workbook()
    gc.book("S").fail_worksheet("state", make_api_error(503))
    assert "Sheets read failed during preflight" in _problems(gc)


def test_preflight_is_read_only():
    gc = workbook()
    gc.book("S")._sheets.pop("Usage")
    P.check_sheets(gc, "S")
    assert gc.writes() == [] and not [c for c in gc.calls if c[1] == "add_worksheet"]


# ── through run_daily: stop safely, report, never repair ────────────────

@pytest.mark.parametrize("mutate, expect", [
    (lambda b: b._sheets.pop("faramir_meta"), "schema version"),
    (lambda b: b._sheets.pop("Usage"), "required tab 'Usage' is missing"),
])
def test_daily_run_stops_before_any_write_and_repairs_nothing(env, mutate, expect):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    mutate(env["gc"].book("S"))
    tabs_before = set(env["gc"].book("S")._sheets)
    assert main(env) == 3
    assert env["gc"].writes() == [] and set(env["gc"].book("S")._sheets) == tabs_before
    assert fake.calls == [] and env["slack"].messages == []
    (alert,) = env["slack"].webhook_posts
    assert "preflight failed" in json.dumps(alert["payload"]) and expect in json.dumps(alert["payload"])
    report, _, _ = artifacts(env)
    assert report["preflight"]["ok"] is False and report["halted_reason"].startswith("preflight")


def test_live_run_without_enable_does_not_start(env):
    env["monkeypatch"].delenv("FARAMIR_ENABLED")
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 0
    assert env["gc"].calls == [] and env["http"].calls == []
    report, _, _ = artifacts(env)
    assert "FARAMIR_ENABLED" in report["not_started"]


def test_dry_run_works_while_disabled(env):
    from tests.test_run_daily_corpus import pick_first_candidate

    env["monkeypatch"].delenv("FARAMIR_ENABLED")
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env, "--dry-run") == 0
    assert env["gc"].writes() == [] and env["slack"].messages == []
    report, _, _ = artifacts(env)
    assert report["preflight"]["ok"] is True and report["mode"] == "dry_run"
