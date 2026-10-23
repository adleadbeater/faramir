"""Increment 5: suggestions tab — idempotent appends, safe feedback updates,
legacy rows preserved, suppression unchanged."""

import importlib.util
import json
import os
from datetime import date, timedelta

import pytest
import requests

from faramir import runtime
from faramir.sheet import SUGGESTIONS_HEADERS, SheetReadError
from faramir.suggestions import (
    FEEDBACK_COLUMNS, SuggestionsWriteIncident, append_new, apply_feedback, parse, read_table, suggestion_id,
)
from tests.fakes.fake_gspread import FakeClient, make_api_error

H = list(SUGGESTIONS_HEADERS)
ROOT = os.path.join(os.path.dirname(__file__), "..")
TODAY = date(2026, 10, 22)


def pick(comp=9, active=1, run_date="2026-10-22", axis="domestic", kind="just_passed", **kw):
    row = {"run_date": run_date, "active_tmdb_id": active, "active_title": f"Film {active}",
           "comp_tmdb_id": comp, "comp_title": f"Comp {comp}", "axis": axis, "kind": kind,
           "threshold_value": "", "angle_category": "franchise_sibling", "headline": f"H {active}-{comp}",
           "angle": "a", "richness_score": 10, "slack_ts": "", "thumbs_up": "", "thumbs_down": "",
           "thread_replies": "", "thread_feedback": ""}
    row.update(kw)
    return row


def cells(row):
    return ["" if row.get(h) is None else str(row.get(h, "")) for h in H]


LEGACY = [
    pick(comp=11, active=1, run_date="2026-10-15", slack_ts="100.1", thumbs_up="2", thumbs_down="0",
         thread_replies="1", thread_feedback="good"),
    pick(comp="None", active=2, run_date="2026-10-18", kind="milestone", threshold_value="100000000", slack_ts="100.2",
         thumbs_up="1"),                                                         # partial feedback
    pick(comp=13, active=3, run_date="2026-10-20", slack_ts="100.3"),
    pick(comp=13, active=3, run_date="2026-10-20", slack_ts="100.4"),           # duplicate id (posted twice)
    pick(comp="", active="", run_date="2026-10-20", slack_ts="100.5"),          # missing ids
    pick(comp=14, active=4, run_date="2026-10-21", slack_ts="200.1"),
    pick(comp=15, active=5, run_date="2026-10-21", slack_ts="200.2"),
    pick(comp=16, active=6, run_date="2026-10-21", slack_ts="200.3"),
]


def legacy_book():
    gc = FakeClient()
    gc.book("S").add_tab("suggestions", [H] + [cells(r) for r in LEGACY])
    return gc


def ws(gc):
    return gc.book("S").worksheet("suggestions")


# ── identity ────────────────────────────────────────────────────────────

def test_suggestion_id_is_stable_and_normalises_sheet_artifacts():
    a = suggestion_id(pick(comp=9))
    assert a == suggestion_id(pick(comp="9")) == suggestion_id({**pick(), "comp_tmdb_id": "9.0"})
    assert suggestion_id(pick(comp="None", kind="milestone", threshold_value=100000000)) == \
        suggestion_id(pick(comp="", kind="milestone", threshold_value="100000000"))
    assert suggestion_id(pick(comp=9, axis="worldwide")) != a
    assert suggestion_id(pick(active="")) is None


def test_legacy_table_ambiguity_is_reported_not_merged():
    t = read_table(legacy_book(), "S")
    assert len(t.rows) == len(LEGACY)
    assert t.unidentifiable == [6]
    assert list(t.duplicate_ids.values()) == [[4, 5]]
    assert t.duplicate_ts == {}


# ── suppression unchanged ───────────────────────────────────────────────

def _original_suppression(past_suggestions, today):
    """Verbatim copy of the pre-v2 Step 9 logic (scripts/run_daily.py @ f2a0ce4)."""
    suppress_cutoff = today - timedelta(days=5)
    suppressed_pairs = set()
    for s in past_suggestions:
        try:
            run_d = date.fromisoformat(str(s.get("run_date", "")))
        except ValueError:
            continue
        if run_d >= suppress_cutoff:
            raw_comp_id = s.get("comp_tmdb_id", "")
            threshold = s.get("threshold_value", "")
            comp_id_str = str(raw_comp_id).strip() if raw_comp_id is not None else ""
            if comp_id_str in ("", "None", "0"):
                comp_id_str = ""
            if comp_id_str:
                try:
                    suppressed_pairs.add((int(s.get("active_tmdb_id") or 0), int(comp_id_str), s.get("axis", "")))
                except ValueError:
                    pass
            elif threshold and str(threshold).strip() not in ("", "None"):
                suppressed_pairs.add((int(s.get("active_tmdb_id") or 0), 0, s.get("axis", "")))
    return suppressed_pairs


def _run_daily():
    spec = importlib.util.spec_from_file_location("run_daily", os.path.join(ROOT, "scripts", "run_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_suppression_identical_on_legacy_rows():
    gc = legacy_book()
    old_records = ws(gc).get_all_records()          # what read_suggestions returned before
    new_records = read_table(gc, "S").records()
    expected = _original_suppression(old_records, TODAY)
    assert expected  # the fixture exercises the window
    assert _run_daily()._suppressed_pairs(new_records, TODAY, 5) == expected


# ── idempotent append ───────────────────────────────────────────────────

NEW = [pick(comp=21, active=7), pick(comp=22, active=8)]


def test_append_single_call_and_ids_once():
    gc = legacy_book()
    before = ws(gc).data
    res = append_new(gc, "S", NEW)
    assert [c[1] for c in gc.writes()] == ["append_rows"] and res.attempts == 1
    assert ws(gc).data[: len(before)] == before                 # history untouched, same order
    assert len(read_table(gc, "S").rows) == len(LEGACY) + 2


def test_append_that_landed_but_timed_out_is_not_repeated():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", requests.Timeout("read timed out"), partial=2)  # both rows landed
    res = append_new(gc, "S", NEW)
    assert [c[1] for c in gc.writes()] == ["append_rows"]        # no retry append
    assert sorted(res.appended) == sorted(suggestion_id(r) for r in NEW)
    ids = read_table(gc, "S").ids()
    assert all(len(ids[suggestion_id(r)]) == 1 for r in NEW)


def test_append_that_never_landed_is_retried_once():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", make_api_error(503), partial=0)
    res = append_new(gc, "S", NEW)
    assert res.attempts == 2 and [c[1] for c in gc.writes()] == ["append_rows", "append_rows"]
    assert all(len(v) == 1 for k, v in read_table(gc, "S").ids().items() if k in {suggestion_id(r) for r in NEW})


def test_partially_landed_append_retries_only_the_missing_row():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", requests.Timeout(), partial=1)
    append_new(gc, "S", NEW)
    second = [c for c in gc.writes() if c[1] == "append_rows"][1]
    assert len(second[2][0]) == 1  # only the row that had not landed
    ids = read_table(gc, "S").ids()
    assert all(len(ids[suggestion_id(r)]) == 1 for r in NEW)


def test_same_day_rerun_appends_nothing():
    gc = legacy_book()
    append_new(gc, "S", NEW)
    n = len(gc.writes())
    res = append_new(gc, "S", NEW)
    assert len(gc.writes()) == n and len(res.already_present) == 2


def test_duplicate_pick_within_a_run_is_logged_once():
    gc = legacy_book()
    append_new(gc, "S", [NEW[0], dict(NEW[0])])
    assert len(read_table(gc, "S").ids()[suggestion_id(NEW[0])]) == 1


def test_reconciliation_read_failure_stops_without_blind_retry():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", requests.Timeout(), partial=0)
    reads = {"n": 0}
    real = ws(gc).get_all_values

    def flaky_read():
        reads["n"] += 1
        if reads["n"] == 2:
            raise make_api_error(503)
        return real()

    ws(gc).get_all_values = flaky_read
    with pytest.raises(SheetReadError):
        append_new(gc, "S", NEW)
    assert [c[1] for c in gc.writes()] == ["append_rows"]


def test_persistent_failure_gives_up_after_three_attempts_without_duplicates():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", make_api_error(503), partial=0)
    real = ws(gc).append_rows

    def always_fail(values, **kw):
        ws(gc)._client.calls.append(("suggestions", "append_rows", (values,)))
        raise make_api_error(503)

    ws(gc).append_rows = always_fail
    with pytest.raises(RuntimeError, match="incomplete after 3 attempts"):
        append_new(gc, "S", NEW)
    ws(gc).append_rows = real
    assert len(read_table(gc, "S").rows) == len(LEGACY)


def test_empty_tab_gets_header_once_even_after_timeout():
    gc = FakeClient()
    gc.book("S").add_tab("suggestions")
    ws(gc).fail_on("append_rows", requests.Timeout(), partial=3)  # header + 2 rows landed
    append_new(gc, "S", NEW)
    from faramir.sheet import SUGGESTIONS_HEADERS_V2

    data = ws(gc).data
    assert data[0] == SUGGESTIONS_HEADERS_V2 and sum(1 for r in data if r == SUGGESTIONS_HEADERS_V2) == 1
    assert len(data) == 3


def test_unidentifiable_pick_is_never_retried():
    gc = legacy_book()
    ws(gc).fail_on("append_rows", requests.Timeout(), partial=1)
    res = append_new(gc, "S", [pick(active="", comp="")])
    assert res.unconfirmed_unidentifiable == 1
    assert [c[1] for c in gc.writes()] == ["append_rows"]


def test_append_in_dry_run_and_after_halt_writes_nothing(tmp_path):
    gc = legacy_book()
    ctx = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="d", out_dir=str(tmp_path))
    with runtime.use(ctx):
        append_new(gc, "S", NEW)
    assert gc.writes() == [] and ctx.operations[0]["op"] == "append_suggestions"
    halted = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id="h", out_dir=str(tmp_path))
    halted.halt("corpus ABORT")
    with runtime.use(halted), pytest.raises(runtime.WriteBlocked):
        append_new(gc, "S", NEW)
    assert gc.writes() == []


# ── feedback ────────────────────────────────────────────────────────────

FB = {"200.1": {"thumbs_up": 3, "thumbs_down": 1, "thread_replies": 2, "thread_feedback": "nice"}}


def _changed_cells(before, after):
    return {(i, j) for i, (rb, ra) in enumerate(zip(before, after)) for j, (a, b) in enumerate(zip(rb, ra)) if a != b}


def test_feedback_writes_only_the_four_cells_of_the_right_row():
    gc = legacy_book()
    before = ws(gc).data
    res = apply_feedback(gc, "S", read_table(gc, "S"), FB)
    assert res.updated == ["200.1"]
    changed = _changed_cells(before, ws(gc).data)
    assert {i for i, _ in changed} == {6}  # sheet row 7 = LEGACY[5]
    assert {H[j] for _, j in changed} <= set(FEEDBACK_COLUMNS)


def test_feedback_follows_a_moved_row():
    gc = legacy_book()
    original = read_table(gc, "S")
    rows = ws(gc).rows
    rows[1:] = list(reversed(rows[1:]))  # editor sorts the tab after Faramir's first read
    res = apply_feedback(gc, "S", original, FB)
    assert res.updated == ["200.1"] and res.moved["200.1"][0] == 7
    target = next(r for r in ws(gc).rows if r[H.index("slack_ts")] == "200.1")
    assert target[H.index("thumbs_up")] == "3"


def test_feedback_refused_when_row_missing():
    gc = legacy_book()
    original = read_table(gc, "S")
    del ws(gc).rows[6]
    before = ws(gc).data
    res = apply_feedback(gc, "S", original, FB)
    assert "no longer exists" in res.refused["200.1"] and ws(gc).data == before


def test_feedback_refused_on_duplicated_slack_ts():
    gc = legacy_book()
    ws(gc).rows.append(list(ws(gc).rows[6]))  # row duplicated (copy/paste)
    before = ws(gc).data
    res = apply_feedback(gc, "S", read_table(gc, "S"), FB)
    assert "appears on 2 rows" in res.refused["200.1"] and ws(gc).data == before


def test_feedback_refused_when_ts_now_belongs_to_another_suggestion():
    gc = legacy_book()
    original = read_table(gc, "S")
    ws(gc).rows[6][H.index("comp_tmdb_id")] = "999"  # row edited to a different story
    before = ws(gc).data
    res = apply_feedback(gc, "S", original, FB)
    assert "different suggestion" in res.refused["200.1"] and ws(gc).data == before


def test_feedback_never_blanks_fields_that_were_not_fetched():
    gc = legacy_book()
    # Row 3 (sheet) has partial legacy feedback: thumbs_up=1. Reactions fetch failed;
    # only the replies fields were fetched.
    apply_feedback(gc, "S", read_table(gc, "S"), {"100.2": {"thread_replies": 0, "thread_feedback": ""}})
    row = ws(gc).rows[2]
    assert row[H.index("thumbs_up")] == "1" and row[H.index("thread_replies")] == "0"


def test_unchanged_feedback_is_not_rewritten():
    gc = legacy_book()
    res = apply_feedback(gc, "S", read_table(gc, "S"),
                         {"100.1": {"thumbs_up": 2, "thumbs_down": 0, "thread_replies": 1, "thread_feedback": "good"}})
    assert res.unchanged == ["100.1"] and gc.writes() == []


def test_interrupted_feedback_update_is_safe_to_repeat():
    gc = legacy_book()
    fb = {**FB, "200.2": {"thumbs_up": 5}}
    ws(gc).fail_on("batch_update", requests.Timeout(), partial=2)  # some cells landed, then timeout
    with pytest.raises(requests.Timeout):
        apply_feedback(gc, "S", read_table(gc, "S"), fb)
    res = apply_feedback(gc, "S", read_table(gc, "S"), fb)  # next attempt
    assert set(res.updated) | set(res.unchanged) == {"200.1", "200.2"}
    assert ws(gc).rows[6][H.index("thumbs_up")] == "3" and ws(gc).rows[7][H.index("thumbs_up")] == "5"


def test_tab_reordered_during_the_write_is_detected():
    gc = legacy_book()
    original_batch = ws(gc).batch_update

    def write_then_sort(data, **kw):
        result = original_batch(data, **kw)
        rows = ws(gc).rows
        rows[1:] = list(reversed(rows[1:]))  # someone sorts the tab right after
        return result

    ws(gc).batch_update = write_then_sort
    with pytest.raises(SuggestionsWriteIncident, match="review these rows"):
        apply_feedback(gc, "S", read_table(gc, "S"), FB)


def test_feedback_read_failure_raises():
    gc = legacy_book()
    original = read_table(gc, "S")
    gc.book("S").fail_worksheet("suggestions", make_api_error())
    with pytest.raises(SheetReadError):
        apply_feedback(gc, "S", original, FB)
    assert gc.writes() == []


def test_feedback_in_dry_run_records_only(tmp_path):
    gc = legacy_book()
    ctx = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="d", out_dir=str(tmp_path))
    with runtime.use(ctx):
        res = apply_feedback(gc, "S", read_table(gc, "S"), FB)
    assert res.updated == ["200.1"] and gc.writes() == []
    assert ctx.operations[0]["op"] == "update_feedback_cells"


def test_fetch_feedback_partial_slack_failure(monkeypatch):
    from faramir.feedback import fetch_feedback
    from tests.fakes.fake_http import FakeHTTP, FakeResponse
    from tests.fakes.fake_slack import FakeSlack

    http = FakeHTTP().install(monkeypatch)
    http.route("GET", "https://slack.com/api/reactions.get", FakeResponse(200, {"ok": False, "error": "ratelimited"}))
    FakeSlack().attach(http)
    out = fetch_feedback("xoxb", "C1", [pick(run_date="2026-10-21", slack_ts="200.1")], date(2026, 10, 21))
    assert set(out["200.1"]) == {"thread_replies", "thread_feedback"}


# ── historical rows survive a full daily run ────────────────────────────

from tests.test_run_daily_corpus import V2_PATH, env, pick_first_candidate  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_daily_run_preserves_history_and_reruns_add_no_duplicates(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    from faramir.sheet import DELIVERY_COLUMNS
    from faramir.suggestions import plan_migration

    tab = env["gc"].book("S").worksheet("suggestions")
    legacy = [H] + [cells(r) for r in LEGACY]
    plan = plan_migration(parse(legacy))  # Increment 6: the daily run needs schema v2
    tab.rows = [legacy[0] + DELIVERY_COLUMNS] + [
        legacy[a["row"] - 1] + [a[c] for c in DELIVERY_COLUMNS] for a in plan["assignments"]]
    before = [list(r) for r in tab.rows]
    first = {}

    def remember(kwargs):
        first["text"] = pick_first_candidate(kwargs)
        return first["text"]

    # The rerun's Claude returns the very same pick again (suppression would
    # normally hide it; this forces the duplicate-logging risk).
    FakeAnthropic([remember, lambda kwargs: first["text"]]).install(env["monkeypatch"])
    assert main(env) == 0
    assert tab.rows[: len(before)][0] == before[0]
    for i, old in enumerate(before[1:], start=1):  # every historical row: same position,
        new = tab.rows[i]                           # only feedback cells may differ
        # Pick columns unchanged; feedback and delivery columns may be updated by the run.
        assert new[: len(H)][: H.index("slack_ts")] == old[: len(H)][: H.index("slack_ts")]
        assert new[len(H)] == old[len(H)]  # immutable suggestion_id
    assert len(tab.rows) == len(before) + 1
    assert main(env) == 0                           # same-day rerun
    assert len(tab.rows) == len(before) + 1         # no duplicate suggestion row


def test_daily_run_stops_when_suggestions_cannot_be_read(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["gc"].book("S").fail_worksheet("suggestions", make_api_error())
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert fake.calls == [] and env["slack"].messages == []
    assert env["gc"].writes() == []  # Increment 12: caught by preflight, before the lock
    report, _, _ = artifacts(env)
    assert report["halted_reason"].startswith("preflight: Sheets read failed during preflight: suggestions")


def test_daily_run_stops_when_suggestions_read_fails_after_preflight(env):
    from tests.fakes.fake_anthropic import FakeAnthropic

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["gc"].book("S").worksheet("suggestions").fail_on("get_all_values", make_api_error(), after=1)
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert fake.calls == [] and env["slack"].messages == []
    ops = {c[0] for c in env["gc"].writes()}
    assert ops <= {"state_meta"}  # only the lock acquire/release
    report, _, _ = artifacts(env)
    assert report["halted_reason"].startswith("suggestions read failed")


def test_realistic_slack_ts_with_trailing_zeros_survives_the_feedback_path(monkeypatch):
    """Regression (found in Increment 6): numericised records turned the ts
    1700000001.000100 into 1700000001.0001, so feedback matched no row."""
    from faramir.feedback import fetch_feedback
    from tests.fakes.fake_http import FakeHTTP
    from tests.fakes.fake_slack import FakeSlack

    gc = FakeClient()
    row = pick(comp=31, active=9, run_date="2026-10-21", slack_ts="1700000001.000100")
    gc.book("S").add_tab("suggestions", [H, cells(row)])
    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    slack.reactions["1700000001.000100"] = [{"name": "+1", "count": 4}]
    table = read_table(gc, "S")
    assert table.records()[0]["slack_ts"] != "1700000001.000100"  # the numericised view is lossy...
    fetched = fetch_feedback("xoxb", "C1", [r.raw for r in table.rows], date(2026, 10, 21))
    res = apply_feedback(gc, "S", table, fetched)                  # ...the raw view is exact
    assert res.updated == ["1700000001.000100"]
    assert ws(gc).rows[1][H.index("thumbs_up")] == "4"
