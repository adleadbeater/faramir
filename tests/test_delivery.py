"""Increment 6: Slack delivery — states, crash points, reconciliation, replay,
migration, reruns. Offline only (fakes); no real Slack or Sheets."""

import importlib.util
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from faramir import runtime
from faramir.delivery import (
    DeliveryItem, SlackTarget, deliver, reconcile, should_post_header, suppression_records, triage_undelivered,
)
from faramir.sheet import DELIVERY_COLUMNS, SUGGESTIONS_HEADERS, SUGGESTIONS_HEADERS_V2
from faramir.slack_history import fetch_tagged
from faramir.suggestions import append_new, parse, plan_migration, read_table, suggestion_id, update_by_id
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fakes.fake_http import FakeHTTP
from tests.fakes.fake_slack import FakeSlack

ROOT = os.path.join(os.path.dirname(__file__), "..")
TODAY = date(2026, 10, 22)
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
CH = "C1"


def pick(comp, active=1, run_date="2026-10-22", data_date="2026-10-21", state="planned", **kw):
    row = {"run_date": run_date, "active_tmdb_id": active, "active_title": f"Film {active}", "comp_tmdb_id": comp,
           "comp_title": f"Comp {comp}", "axis": "domestic", "kind": "just_passed", "threshold_value": "",
           "angle_category": "franchise_sibling", "headline": f"Film {active} passes Comp {comp}", "angle": "a",
           "richness_score": 10, "slack_ts": "", "delivery_state": state, "delivery_attempts": "0",
           "last_attempt_at": "", "delivery_run_id": "r0", "data_date": data_date, "delivery_note": ""}
    row.update(kw)
    row["suggestion_id"] = suggestion_id(row)
    return row


@pytest.fixture
def world(monkeypatch):
    gc = FakeClient()
    gc.book("S").add_tab("suggestions")
    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    return gc, http, slack


def log(gc, rows):
    append_new(gc, "S", rows)
    return read_table(gc, "S")


def item(row):
    return DeliveryItem(row["suggestion_id"], row, {"domestic_today": 150_000_000}, None)


def state_of(gc, sid):
    return read_table(gc, "S").by_id()[sid][0].get("delivery_state")


def tagged_posts(slack, sid):
    return slack.tagged().get(sid, [])


T = SlackTarget("xoxb", CH)


# ── Slack history reader ────────────────────────────────────────────────

def _post_n(slack, n, with_metadata=True):
    from faramir.slack_post import post_pick

    ids = []
    for i in range(n):
        r = pick(100 + i)
        res = post_pick("xoxb", CH, r, {}, None, suggestion_id=r["suggestion_id"], run_id="x")
        assert res.status == "posted"
        ids.append(r["suggestion_id"])
    return ids


def test_history_follows_every_page(world):
    _, _, slack = world
    ids = _post_n(slack, 7)
    slack.page_size_cap = 2
    h = fetch_tagged("xoxb", CH, 0)
    assert h.ok and h.pages == 4 and set(h.picks) == set(ids)


def test_history_without_metadata_scope_matches_visible_ref(world):
    _, _, slack = world
    ids = _post_n(slack, 3)
    slack.metadata_readable = False
    h = fetch_tagged("xoxb", CH, 0)
    assert h.ok and set(h.picks) == set(ids) and h.matched_by_ref == 3 and h.matched_by_metadata == 0


@pytest.mark.parametrize("mode", ["missing_scope", "not_in_channel", "ratelimited", "http_500", "timeout", "drop_cursor"])
def test_history_failures_are_never_complete(world, mode):
    _, _, slack = world
    _post_n(slack, 5)
    slack.page_size_cap = 2
    slack.history_failures = [mode]
    h = fetch_tagged("xoxb", CH, 0)
    assert h.ok is False and h.error


def test_history_page_cap_is_not_complete(world):
    _, _, slack = world
    _post_n(slack, 6)
    slack.page_size_cap = 1
    assert fetch_tagged("xoxb", CH, 0, max_pages=3).ok is False


def test_duplicate_metadata_and_human_refs(world):
    _, http, slack = world
    from faramir.slack_post import post_pick

    r = pick(9)
    post_pick("xoxb", CH, r, {}, None, suggestion_id=r["suggestion_id"])
    post_pick("xoxb", CH, r, {}, None, suggestion_id=r["suggestion_id"])  # duplicated in Slack
    slack.messages.append({"ts": "1800000000.0001", "text": f"ref {pick(77)['suggestion_id']}", "type": "message",
                           "user": "U123"})  # a human quoting a ref is not a delivery
    h = fetch_tagged("xoxb", CH, 0)
    assert len(h.picks[r["suggestion_id"]]) == 2 and pick(77)["suggestion_id"] not in h.picks


# ── crash points in log -> post -> confirm ──────────────────────────────

def reconcile_now(gc, slack_target=T, now=NOW + timedelta(hours=1)):
    return reconcile(gc, "S", read_table(gc, "S"), slack_target, TODAY, now)


def test_normal_delivery_is_logged_posted_confirmed(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    (out,) = deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    row = read_table(gc, "S").by_id()[r["suggestion_id"]][0]
    assert out.state == "posted" and row.get("delivery_state") == "posted"
    assert row.get("slack_ts") == tagged_posts(slack, r["suggestion_id"])[0] and row.get("delivery_attempts") == "1"


def test_crash_before_posting_mark_fails_nothing_posted_then_replayed_once(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    gc.book("S").worksheet("suggestions").fail_on("batch_update", make_api_error(503))
    with pytest.raises(Exception):
        deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert slack.messages == [] and state_of(gc, r["suggestion_id"]) == "planned"
    rec = reconcile_now(gc)
    assert rec.changed == {r["suggestion_id"]: "not_delivered"}
    tri = triage_undelivered(read_table(gc, "S"), TODAY, date(2026, 10, 21), {"9"})
    assert [x.sid for x in tri.replay] == [r["suggestion_id"]]
    deliver(gc, "S", T, [DeliveryItem(r["suggestion_id"], r, {}, None, attempts_before=0, replay=True)], "run2")
    assert len(tagged_posts(slack, r["suggestion_id"])) == 1


def test_attempted_but_absent_becomes_unconfirmed_never_auto_retried(world):
    # Increment 7 (review requirement): absence from history after an attempt
    # is not proof of non-delivery. It becomes 'unconfirmed': still suppressing,
    # listed for review, never retried automatically.
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    slack.fail_next_post(timeout=True, deliver=False)
    (out,) = deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert out.state == "unknown" and state_of(gc, r["suggestion_id"]) == "unknown"
    assert reconcile_now(gc, now=NOW + timedelta(minutes=1)).still_open == [r["suggestion_id"]]  # within grace
    assert reconcile_now(gc, now=NOW + timedelta(minutes=10)).changed == {r["suggestion_id"]: "unconfirmed"}
    table = read_table(gc, "S")
    assert triage_undelivered(table, TODAY, date(2026, 10, 21), {"9"}).replay == []
    assert r["suggestion_id"] in {x["suggestion_id"] for x in suppression_records(table)}
    assert slack.messages == []


def test_slack_accepted_but_response_timed_out_is_confirmed_not_reposted(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    slack.fail_next_post(timeout=True, deliver=True)
    (out,) = deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert out.state == "unknown"
    rec = reconcile_now(gc)
    assert rec.changed == {r["suggestion_id"]: "posted"}
    row = read_table(gc, "S").by_id()[r["suggestion_id"]][0]
    assert row.get("slack_ts") == tagged_posts(slack, r["suggestion_id"])[0]
    assert triage_undelivered(read_table(gc, "S"), TODAY, date(2026, 10, 21), {"9"}).replay == []
    assert len(tagged_posts(slack, r["suggestion_id"])) == 1


def test_confirmation_write_fails_after_post_is_reconciled(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    ws = gc.book("S").worksheet("suggestions")
    ws.fail_on("batch_update", make_api_error(503), after=1)  # mark ok, confirm fails
    (out,) = deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert out.state == "posted" and out.confirmed is False
    assert state_of(gc, r["suggestion_id"]) == "sending"
    assert reconcile_now(gc).changed == {r["suggestion_id"]: "posted"}
    assert len(tagged_posts(slack, r["suggestion_id"])) == 1


def test_partial_sheets_confirmation_across_two_picks(world):
    gc, _, slack = world
    a, b = pick(9), pick(10)
    log(gc, [a, b])
    ws = gc.book("S").worksheet("suggestions")
    ws.fail_on("batch_update", make_api_error(503), after=3)  # a: mark, confirm; b: mark ok, confirm fails
    outs = deliver(gc, "S", T, [item(a), item(b)], "run1", now_fn=lambda: NOW)
    assert [o.confirmed for o in outs] == [True, False]
    assert (state_of(gc, a["suggestion_id"]), state_of(gc, b["suggestion_id"])) == ("posted", "sending")
    reconcile_now(gc)
    assert state_of(gc, b["suggestion_id"]) == "posted"


def test_slack_rejection_is_failed_then_replayed_same_day(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    slack.fail_next_post("channel_is_archived")
    (out,) = deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert out.state == "failed" and slack.messages == []
    tri = triage_undelivered(read_table(gc, "S"), TODAY, date(2026, 10, 21), {"9"})
    assert [x.sid for x in tri.replay] == [r["suggestion_id"]]


def test_history_failure_resolves_nothing_and_never_reposts(world):
    gc, _, slack = world
    r = pick(9, state="unknown", last_attempt_at=(NOW - timedelta(hours=3)).isoformat())
    log(gc, [r])
    slack.history_failures = ["missing_scope"]
    rec = reconcile_now(gc)
    assert rec.history_ok is False and rec.still_open == [r["suggestion_id"]] and rec.changed == {}
    assert state_of(gc, r["suggestion_id"]) == "unknown"
    assert should_post_header(rec, True)[0] is False
    assert r["suggestion_id"] in {x["suggestion_id"] for x in suppression_records(read_table(gc, "S"))}


# ── expiry, triage, suppression ─────────────────────────────────────────

def test_old_or_changed_evidence_expires_for_manual_review(world):
    gc, _, _ = world
    rows = [pick(1, state="not_delivered", run_date="2026-10-21"),           # earlier day
            pick(2, state="not_delivered", data_date="2026-10-20"),          # data moved
            pick(3, state="failed", delivery_attempts="3"),                  # attempt limit
            pick(4, state="not_delivered"),                                  # comp no longer usable
            pick(5, state="not_delivered")]                                  # eligible
    log(gc, rows)
    tri = triage_undelivered(read_table(gc, "S"), TODAY, date(2026, 10, 21), {"1", "2", "3", "5"})
    assert [x.sid for x in tri.replay] == [rows[4]["suggestion_id"]]
    assert "earlier day" in tri.expired[rows[0]["suggestion_id"]]
    assert "box office data has changed" in tri.expired[rows[1]["suggestion_id"]]
    assert "3 attempts" in tri.expired[rows[2]["suggestion_id"]]
    assert "no longer usable" in tri.expired[rows[3]["suggestion_id"]]


def test_suppression_excludes_only_definitely_undelivered(world):
    gc, _, _ = world
    states = ["posted", "planned", "sending", "unknown", "legacy_unknown", "not_delivered", "expired", "failed", "dismissed"]
    log(gc, [pick(i, state=s) for i, s in enumerate(states, start=1)])
    kept = {x["delivery_state"] for x in suppression_records(read_table(gc, "S"))}
    assert kept == {"posted", "planned", "sending", "unknown", "legacy_unknown"}


# ── by-id updates: moved, duplicated, edited rows ───────────────────────

def test_update_by_id_follows_a_sorted_tab(world):
    gc, _, _ = world
    rows = [pick(1), pick(2), pick(3)]
    log(gc, rows)
    ws = gc.book("S").worksheet("suggestions")
    ws.rows[1:] = list(reversed(ws.rows[1:]))
    res = update_by_id(gc, "S", {rows[0]["suggestion_id"]: {"delivery_note": "x"}}, "t")
    assert res.updated == [rows[0]["suggestion_id"]]
    assert read_table(gc, "S").by_id()[rows[0]["suggestion_id"]][0].get("delivery_note") == "x"


def test_duplicated_id_row_is_refused_and_not_posted(world):
    gc, _, slack = world
    r = pick(9)
    log(gc, [r])
    ws = gc.book("S").worksheet("suggestions")
    ws.rows.append(list(ws.rows[1]))
    (out,) = deliver(gc, "S", T, [item(r)], "run1")
    assert out.state == "not_attempted" and slack.messages == []


def test_edited_pick_columns_are_refused(world):
    gc, _, _ = world
    r = pick(9)
    log(gc, [r])
    ws = gc.book("S").worksheet("suggestions")
    ws.rows[1][SUGGESTIONS_HEADERS_V2.index("comp_tmdb_id")] = "12345"
    res = update_by_id(gc, "S", {r["suggestion_id"]: {"delivery_note": "x"}}, "t")
    assert "no longer match" in res.refused[r["suggestion_id"]]


def test_update_by_id_never_writes_pick_or_id_columns(world):
    gc, _, _ = world
    r = pick(9)
    log(gc, [r])
    for col in ("headline", "suggestion_id", "comp_tmdb_id"):
        with pytest.raises(ValueError):
            update_by_id(gc, "S", {r["suggestion_id"]: {col: "x"}}, "t")


def test_sort_during_by_id_write_is_an_incident(world):
    from faramir.suggestions import SuggestionsWriteIncident

    gc, _, _ = world
    rows = [pick(1), pick(2)]
    log(gc, rows)
    ws = gc.book("S").worksheet("suggestions")
    real = ws.batch_update

    def write_then_sort(data, **kw):
        out = real(data, **kw)
        ws.rows[1:] = list(reversed(ws.rows[1:]))
        return out

    ws.batch_update = write_then_sort
    with pytest.raises(SuggestionsWriteIncident):
        update_by_id(gc, "S", {rows[0]["suggestion_id"]: {"delivery_note": "x"}}, "t")


# ── migration of legacy rows ────────────────────────────────────────────

def legacy_values():
    from tests.test_suggestions import LEGACY, cells

    return [list(SUGGESTIONS_HEADERS)] + [cells(r) for r in LEGACY]


def test_migration_plan_assigns_stable_unique_ids():
    plan = plan_migration(parse(legacy_values()))
    ids = [a["suggestion_id"] for a in plan["assignments"]]
    assert len(ids) == len(set(ids)) == 8
    assert ids[3] == ids[2] + "~2"                                  # legacy duplicate kept, suffixed
    assert ids[4].startswith("legacy-")                             # no pick ids
    assert plan["assignments"][0]["delivery_state"] == "posted"     # has a Slack ts
    assert plan_migration(parse(legacy_values())) == plan            # deterministic


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy_sheet(monkeypatch, tmp_path):
    from faramir import sheet

    gc = FakeClient()
    gc.book("S").add_tab("suggestions", legacy_values())
    monkeypatch.setattr(sheet, "get_sheet_client", lambda: gc)
    monkeypatch.setenv("FARAMIR_SHEET_ID", "S")
    return gc, tmp_path


def test_migration_plan_is_read_only(legacy_sheet, capsys):
    gc, _ = legacy_sheet
    assert load_script("migrate_suggestions").main([]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 8 and gc.writes() == []


def test_migration_apply_preserves_every_existing_cell(legacy_sheet, monkeypatch):
    gc, tmp = legacy_sheet
    before = [list(r) for r in gc.book("S").worksheet("suggestions").rows]
    mig = load_script("migrate_suggestions")
    assert mig.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 4  # disabled
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    assert mig.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    after = gc.book("S").worksheet("suggestions").rows
    assert [r[:17] for r in after] == [r[:17] for r in before]       # 17 original columns untouched
    t = read_table(gc, "S")
    assert t.schema == 2 and all(r.sid for r in t.rows)
    assert mig.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0  # idempotent


def test_migration_detects_sort_during_write_and_repair_fixes_it(legacy_sheet, monkeypatch):
    gc, tmp = legacy_sheet
    monkeypatch.setenv("FARAMIR_ENABLED", "true")
    ws = gc.book("S").worksheet("suggestions")
    real = ws.batch_update

    def write_then_sort(data, **kw):
        out = real(data, **kw)
        ws.rows[1:] = sorted(ws.rows[1:], key=lambda r: r[0])[::-1]
        ws.rows[2], ws.rows[3] = [c for c in ws.rows[2]], [c for c in ws.rows[3]]
        # swap only the new-column block between two rows (the id now sits on the wrong row)
        a, b = ws.rows[2], ws.rows[5]
        a[17:], b[17:] = b[17:], a[17:]
        return out

    ws.batch_update = write_then_sort
    mig = load_script("migrate_suggestions")
    assert mig.main(["--apply", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 5
    ws.batch_update = real
    assert mig.main(["--apply", "--repair", "--confirm-sheet", "S", "--out-root", str(tmp)]) == 0
    t = read_table(gc, "S")
    assert all(r.sid.startswith(r.derived) for r in t.rows if r.derived)


# ── same-day reruns, header, cap, concurrency (full pipeline) ───────────

from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, pick_first_candidate, pick_msgs  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def _clear_out(env):
    for p in list((env["tmp"] / "out").iterdir()):
        __import__("shutil").rmtree(p)


def test_rerun_with_identical_pick_reposts_nothing_and_no_second_header(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    first = {}

    def remember(kw):
        first["t"] = pick_first_candidate(kw)
        return first["t"]

    FakeAnthropic([remember, lambda kw: first["t"]]).install(env["monkeypatch"])
    assert main(env) == 0
    _clear_out(env)
    assert main(env) == 0
    assert len(pick_msgs(env["slack"])) == 1 and len(header_msgs(env["slack"])) == 1
    report, _, _ = artifacts(env)
    # Increment 7: the repeated pick no longer reaches delivery at all — its pair is
    # suppressed, so it is not a candidate and binding rejects it.
    (rejected,) = report["claude_binding"]["rejected"]
    assert "not a candidate" in rejected["reason"]
    assert report["delivery"]["new"] == []


def test_rerun_with_a_genuinely_new_pick_delivers_it(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate, pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    _clear_out(env)
    assert main(env) == 0  # suppression hides the first pick; Claude picks the next candidate
    sids = {(m["metadata"]["event_payload"]["suggestion_id"]) for m in pick_msgs(env["slack"])}
    assert len(pick_msgs(env["slack"])) == 2 and len(sids) == 2
    assert len(header_msgs(env["slack"])) == 1


def test_daily_cap_counts_earlier_runs(env):
    from tests.fakes.fake_anthropic import FakeAnthropic
    from faramir import config

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    dow = TODAY.weekday()
    env["monkeypatch"].setitem(config.PICKS_MAX_BY_DAY, dow, 1)
    FakeAnthropic([pick_first_candidate, pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    _clear_out(env)
    assert main(env) == 0
    assert len(pick_msgs(env["slack"])) == 1
    report, _, _ = artifacts(env)
    assert len(report["delivery"]["over_daily_cap"]) == 1


def test_history_unavailable_skips_header_but_keeps_new_picks_safe(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["slack"].history_failures = ["missing_scope"]
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    assert header_msgs(env["slack"]) == [] and len(pick_msgs(env["slack"])) == 1
    report, _, _ = artifacts(env)
    assert report["delivery_reconciliation"]["history_ok"] is False


def test_concurrent_run_holding_the_lock_posts_nothing(env):
    from tests.fakes.fake_anthropic import FakeAnthropic
    from faramir.state_store import StateStore

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    other = StateStore(env["gc"], "S", "manual-dispatch")
    other.open()
    other.acquire_lock()
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert pick_msgs(env["slack"]) == [] and header_msgs(env["slack"]) == []


def test_unmigrated_suggestions_tab_stops_the_run(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["gc"].book("S").worksheet("suggestions").rows = legacy_values()
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3 and fake.calls == []
    assert not [c for c in env["gc"].writes() if c[0] == "suggestions"]
    report, _, _ = artifacts(env)
    assert "schema v2" in report["halted_reason"]


def test_dry_run_slack_preview_carries_ref_and_metadata(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env, "--dry-run") == 0
    assert env["gc"].writes() == [] and [c for c in env["http"].calls if c["method"] == "POST"] == []
    _, writes, preview = artifacts(env)
    post = [w for w in writes if w["op"] == "chat.postMessage" and (w["detail"]["metadata"] or {}).get("event_type") == "faramir_pick"]
    assert len(post) == 1
    assert "ref " in json.dumps(preview[-1]["blocks"])


# ── manual replay script ────────────────────────────────────────────────

@pytest.fixture
def replay_env(env):
    from faramir import sheet

    env["monkeypatch"].setattr(sheet, "get_sheet_client", lambda: env["gc"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    r = pick(1924, active=5001, state="expired", run_date="2026-10-20", delivery_note="expired: picked on an earlier day")
    append_new(env["gc"], "S", [r])
    return env, r


def test_replay_list_and_plan_are_read_only(replay_env, capsys):
    env, r = replay_env
    n = len(env["gc"].writes())
    rp = load_script("replay_picks")
    assert rp.main([]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
    assert rp.main(["--plan", "--id", r["suggestion_id"]]) == 0
    assert "Delayed suggestion from 2026-10-20" in capsys.readouterr().out
    assert len(env["gc"].writes()) == n and env["slack"].messages == []


def test_replay_apply_requires_enable_and_reliable_history(replay_env):
    env, r = replay_env
    rp = load_script("replay_picks")
    args = ["--apply", "--id", r["suggestion_id"], "--confirm-channel", "C1", "--out-root", str(env["tmp"] / "o")]
    env["monkeypatch"].delenv("FARAMIR_ENABLED")
    assert rp.main(args) == 4
    env["monkeypatch"].setenv("FARAMIR_ENABLED", "true")
    env["slack"].history_failures = ["ratelimited"]
    assert rp.main(args) == 7 and env["slack"].messages == []
    assert rp.main(args) == 0
    (msg,) = pick_msgs(env["slack"])
    assert "Delayed suggestion" in json.dumps(msg["blocks"])
    assert state_of(env["gc"], r["suggestion_id"]) == "posted"
    assert rp.main(args) == 6  # now posted: refuses to repost


def test_replay_refuses_when_already_in_slack(replay_env):
    env, r = replay_env
    from faramir.slack_post import post_pick

    post_pick("xoxb", "C1", r, {}, None, suggestion_id=r["suggestion_id"])  # it was delivered after all
    env["monkeypatch"].setenv("FARAMIR_ENABLED", "true")
    rp = load_script("replay_picks")
    assert rp.main(["--apply", "--id", r["suggestion_id"], "--confirm-channel", "C1", "--out-root", str(env["tmp"] / "o")]) == 8
    assert len(pick_msgs(env["slack"])) == 1 and state_of(env["gc"], r["suggestion_id"]) == "posted"


def test_dismiss(replay_env):
    env, r = replay_env
    env["monkeypatch"].setenv("FARAMIR_ENABLED", "true")
    rp = load_script("replay_picks")
    assert rp.main(["--dismiss", "--id", r["suggestion_id"], "--reason", "covered", "--out-root", str(env["tmp"] / "o")]) == 0
    assert state_of(env["gc"], r["suggestion_id"]) == "dismissed"


def test_unconfirmed_replay_requires_explicit_channel_check(replay_env):
    env, r = replay_env
    update_by_id(env["gc"], "S", {r["suggestion_id"]: {"delivery_state": "unconfirmed"}}, "t")
    env["monkeypatch"].setenv("FARAMIR_ENABLED", "true")
    rp = load_script("replay_picks")
    base = ["--apply", "--id", r["suggestion_id"], "--confirm-channel", "C1", "--out-root", str(env["tmp"] / "o")]
    assert rp.main(base) == 10 and pick_msgs(env["slack"]) == []
    assert rp.main(base + ["--checked-channel"]) == 0 and len(pick_msgs(env["slack"])) == 1


def test_precision_damaged_slack_ts_is_identified_in_legacy_rows():
    from tests.test_suggestions import LEGACY, cells

    rows = [dict(LEGACY[0], slack_ts="1700000001.0001"), dict(LEGACY[5], slack_ts="1700000002.000100")]
    table = parse([list(SUGGESTIONS_HEADERS)] + [cells(r) for r in rows])
    assert table.damaged_ts == [2]
    plan = plan_migration(table)
    assert plan["precision_damaged_slack_ts_rows"] == [2]
    assert "precision damaged" in plan["assignments"][0]["delivery_note"]


def test_slack_ts_is_written_as_raw_text(world):
    gc, _, _ = world
    r = pick(9)
    log(gc, [r])
    update_by_id(gc, "S", {r["suggestion_id"]: {"slack_ts": "1792670001.000100"}}, "t")
    batch = [c for c in gc.writes() if c[1] == "batch_update"][-1]
    assert batch[2][0][0]["values"] == [["1792670001.000100"]]  # a string, sent with value_input_option=RAW
    assert read_table(gc, "S").by_id()[r["suggestion_id"]][0].get("slack_ts") == "1792670001.000100"
