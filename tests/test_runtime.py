"""Increment 3: the run context and the central write guard."""

import pytest

from faramir import runtime
from faramir.runtime import DRY_RUN, LIVE, RunContext, WriteBlocked, authorize
from tests.fakes.fake_gspread import FakeClient
from tests.fakes.fake_http import FakeHTTP
from tests.fakes.fake_slack import FakeSlack


def ctx(mode=LIVE, enabled=True, tmp=None):
    return RunContext(mode=mode, enabled=enabled, run_id="t", out_dir=str(tmp or "/nonexistent"))


# ── fail-closed enable switch ───────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("false", False), ("False", False), ("0", False), ("1", False),
    ("yes", False), ("enabled", False), ("true", True), ("TRUE", True), (" True ", True),
])
def test_enable_flag_requires_explicit_true(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FARAMIR_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FARAMIR_ENABLED", value)
    assert runtime.env_flag("FARAMIR_ENABLED") is expected
    assert runtime.new_context(dry_run=False).may_run()[0] is expected


def test_dry_run_allowed_while_disabled(monkeypatch):
    monkeypatch.delenv("FARAMIR_ENABLED", raising=False)
    assert runtime.new_context(dry_run=True).may_run() == (True, "dry-run")


def test_fixtures_require_dry_run(monkeypatch):
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    ok, why = runtime.new_context(dry_run=False, fixtures_dir="x").may_run()
    assert not ok and "dry-run" in why


# ── authorize() ─────────────────────────────────────────────────────────

@pytest.mark.no_run_context
def test_no_context_blocks_every_write():
    for category in ("sheets", "slack", "alert", "repo"):
        with pytest.raises(WriteBlocked):
            authorize(category, "op")


def test_unknown_category_rejected():
    with pytest.raises(ValueError):
        authorize("email", "send")


def test_halted_run_blocks_all_but_alerts():
    c = ctx()
    c.halt("corpus ABORT")
    with runtime.use(c):
        for category in ("sheets", "slack", "repo"):
            with pytest.raises(WriteBlocked, match="halted"):
                authorize(category, "op")
        assert authorize("alert", "webhook_post") is True
    outcomes = [(o["category"], o["outcome"]) for o in c.operations]
    assert outcomes == [("sheets", "blocked"), ("slack", "blocked"), ("repo", "blocked"), ("alert", "performed")]


def test_disabled_live_context_blocks_writes():
    with runtime.use(ctx(enabled=False)):
        with pytest.raises(WriteBlocked, match="disabled"):
            authorize("sheets", "write_state")


def test_dry_run_records_and_skips():
    c = ctx(mode=DRY_RUN, enabled=False)
    with runtime.use(c):
        assert authorize("sheets", "write_state", {"rows": 3}) is False
        assert authorize("alert", "webhook_post") is False  # even alerts are not sent in dry-run
    assert [o["outcome"] for o in c.operations] == ["recorded", "recorded"]


# ── Sheets writes go through the guard ──────────────────────────────────

def _book():
    gc = FakeClient()
    gc.book("S").add_tab("state")
    gc.book("S").add_tab("suggestions")
    return gc


@pytest.mark.parametrize("call", [
    lambda s, gc: s.write_values(gc, "S", "state", [["tmdb_id"], ["1"]], "write_state"),
    lambda s, gc: s.append_suggestions(gc, "S", [{"headline": "h"}]),
    lambda s, gc: s.write_suggestions(gc, "S", [{"headline": "h"}]),
    lambda s, gc: s.append_corpus_meta(gc, "S", {"build_date": "x"}),
])
def test_sheet_writes_in_dry_run_touch_nothing(call):
    from faramir import sheet

    gc = _book()
    c = ctx(mode=DRY_RUN)
    with runtime.use(c):
        call(sheet, gc)
    assert gc.calls == [] and c.operations[0]["outcome"] == "recorded"


@pytest.mark.parametrize("call", [
    lambda s, gc: s.write_values(gc, "S", "state", [["tmdb_id"], ["1"]], "write_state"),
    lambda s, gc: s.append_suggestions(gc, "S", [{"headline": "h"}]),
    lambda s, gc: s.write_suggestions(gc, "S", [{"headline": "h"}]),
])
def test_sheet_writes_after_halt_raise_and_touch_nothing(call):
    from faramir import sheet

    gc = _book()
    c = ctx()
    c.halt("corpus ABORT")
    with runtime.use(c), pytest.raises(WriteBlocked):
        call(sheet, gc)
    assert gc.calls == []


def test_usage_flush_block_is_not_swallowed_by_fail_open():
    from faramir import sheet

    gc = _book()
    sheet.buffer_usage("x", 1, 1, 0, 0, "m")
    c = ctx()
    c.halt("preflight")
    try:
        with runtime.use(c), pytest.raises(WriteBlocked):
            sheet.flush_usage_to_sheet(gc, "S")
    finally:
        sheet._USAGE_LOG.clear()
    assert gc.calls == []


# ── Slack goes through the guard ────────────────────────────────────────

def _pick():
    return {"active_title": "A", "comp_title": "B", "axis": "domestic", "kind": "just_passed",
            "angle_category": "franchise_sibling", "headline": "H", "angle": "x"}


def test_slack_dry_run_returns_placeholder_ts_and_sends_nothing(monkeypatch):
    from datetime import date

    from faramir.slack_post import post_header, post_pick

    http = FakeHTTP().install(monkeypatch)
    FakeSlack().attach(http)
    c = ctx(mode=DRY_RUN)
    with runtime.use(c):
        post_header("https://hooks.slack.com/x", 1, 1, date(2026, 10, 22))
        assert post_pick("xoxb", "C1", _pick(), {}, None) == "dryrun-1"
    assert http.calls == []
    assert [p["kind"] for p in c.slack_preview] == ["webhook:slack", "chat.postMessage"]
    assert not any(p["sent"] for p in c.slack_preview)


def test_slack_after_halt_only_alerts_go_out(monkeypatch):
    from datetime import date

    from faramir.slack_post import post_corpus_abort, post_header

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    c = ctx()
    c.halt("corpus ABORT")
    with runtime.use(c):
        with pytest.raises(WriteBlocked):
            post_header("https://hooks.slack.com/x", 0, 0, date(2026, 10, 22))
        post_corpus_abort("https://hooks.slack.com/x", ["legacy_format"], date(2026, 10, 22))
    assert len(slack.webhook_posts) == 1 and "failed integrity" in str(slack.webhook_posts[0])


def test_artifacts_written(tmp_path):
    c = ctx(mode=DRY_RUN, tmp=tmp_path / "run")
    with runtime.use(c):
        authorize("sheets", "write_state", {"rows": 1})
        runtime.note("candidates", 3)
    out = c.write_artifacts()
    import json
    import os

    report = json.load(open(os.path.join(out, "report.json")))
    assert report["mode"] == "dry_run" and report["candidates"] == 3 and report["write_outcomes"] == {"recorded": 1}
    assert json.load(open(os.path.join(out, "would_write.json")))[0]["op"] == "write_state"
