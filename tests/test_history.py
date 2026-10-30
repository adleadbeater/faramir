"""Increment 11: weekend-history transaction protocol (§5.2). In-memory Sheets
and synthetic charts only; collection is enabled per test, never by default."""

from datetime import date, datetime, timedelta, timezone

import pytest

from faramir import history as H
from faramir import runtime
from faramir.tn_chart import parse_chart, weekend_url
from tests.fakes.fake_gspread import FakeClient, make_api_error
from tests.fixtures.tn_synth import WEEKEND_FILMS, page

FRI = date(2026, 10, 16)
T0 = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)     # Sunday afternoon retrieval
TUE = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)    # the following Tuesday


class Crash(Exception):
    pass


def chart(films=None, day=FRI, kind="weekend", **kw):
    return parse_chart(page(kind, day, films=films, **kw), weekend_url(FRI), "weekend", FRI)


def revised(index=1, total=59_400_000):
    films = [list(f) for f in WEEKEND_FILMS]
    films[index][7] = total
    return chart([tuple(f) for f in films])


@pytest.fixture
def gc(monkeypatch):
    monkeypatch.setenv("FARAMIR_COLLECT_HISTORY", "true")
    c = FakeClient()
    c.book("S").add_tab(H.HISTORY_TAB, [H.HISTORY_HEADERS])
    c.book("S").add_tab(H.COLLECTIONS_TAB, [H.COLLECTION_HEADERS])
    return c


def tab(gc, name):
    return gc.book("S").worksheet(name)


def collect(gc, c=None, run="run-1", at=T0, now=None, **kw):
    return H.collect(gc, "S", c or chart(), run_id=run, retrieved_at=at, now=now or at, **kw)


def events(gc, kind=None):
    rows = [dict(zip(H.COLLECTION_HEADERS, r)) for r in tab(gc, H.COLLECTIONS_TAB).rows[1:]]
    return [r for r in rows if kind is None or r["event"] == kind]


def physical_rows(gc):
    return len(tab(gc, H.HISTORY_TAB).rows) - 1


def snap(gc, weekend="2026-10-16"):
    return H.read_weekend(gc, "S", weekend)


N = len(WEEKEND_FILMS)


# ── H1 / H2 ─────────────────────────────────────────────────────────────

def test_h1_normal_collection(gc):
    r = collect(gc)
    assert (r.status, r.revision, r.rows_written) == ("committed", 1, N)
    assert [e["event"] for e in events(gc)] == ["begin", "commit"]
    s = snap(gc)
    assert (s.status, s.revision, len(s.rows), s.collection_status) == ("available", 1, N, "provisional")
    assert events(gc, "commit")[0]["verified_rows"] == str(N)


def test_h2_same_content_twice_writes_no_rows(gc):
    first = collect(gc)
    again = collect(gc, run="run-2", at=T0 + timedelta(hours=6))
    assert (again.status, again.collection_id, again.rows_written) == ("unchanged", first.collection_id, 0)
    assert physical_rows(gc) == N and len(events(gc, "observe")) == 1   # the retrieval is evidence
    assert len(snap(gc).observations) == 2


# ── crashes: never a falsely complete weekend, never orphaned rows ──────

def test_h3_crash_after_begin_before_rows(gc):
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=0)
    with pytest.raises(Crash):
        collect(gc)
    assert snap(gc).status == "missing" and physical_rows(gc) == 0
    r = collect(gc, run="run-2", at=T0 + timedelta(hours=1))
    assert (r.status, r.rows_written) == ("resumed_committed", N)
    assert len(events(gc, "begin")) == 1 and len(events(gc, "commit")) == 1
    assert len(snap(gc).rows) == N


@pytest.mark.parametrize("k", [1, 3, N - 1])
def test_h4_crash_midway_appends_only_missing_rows(gc, k):
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=k)
    with pytest.raises(Crash):
        collect(gc)
    assert snap(gc).status == "missing"  # k rows exist but are invisible
    r = collect(gc, run="run-2", at=T0 + timedelta(hours=1))
    assert (r.status, r.rows_written) == ("resumed_committed", N - k)
    assert physical_rows(gc) == N and len(snap(gc).rows) == N


def test_h5_crash_after_rows_before_commit(gc):
    tab(gc, H.COLLECTIONS_TAB).fail_on("append_rows", Crash(), after=1, partial=0)  # begin lands, commit fails
    with pytest.raises(Crash):
        collect(gc)
    assert physical_rows(gc) == N and snap(gc).status == "missing"
    r = collect(gc, run="run-2", at=T0 + timedelta(hours=1))
    assert (r.status, r.rows_written) == ("resumed_committed", 0)
    assert len(snap(gc).rows) == N


def test_h6_append_acknowledgement_lost(gc):
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=N)  # all rows landed, client saw an error
    with pytest.raises(Crash):
        collect(gc)
    r = collect(gc, run="run-2", at=T0 + timedelta(hours=1))
    assert (r.status, r.rows_written) == ("resumed_committed", 0) and physical_rows(gc) == N


def test_h6_duplicate_physical_rows_are_deduplicated(gc):
    collect(gc)
    ws = tab(gc, H.HISTORY_TAB)
    ws.rows += [list(r) for r in ws.rows[1:4]]  # e.g. a retried append that also landed
    s = snap(gc)
    assert physical_rows(gc) == N + 3 and len(s.rows) == N and s.status == "available"


def test_crash_on_commit_event_append_then_concurrent_resume_commits_once(gc):
    tab(gc, H.COLLECTIONS_TAB).fail_on("append_rows", Crash(), after=1, partial=1)  # commit landed, error seen
    with pytest.raises(Crash):
        collect(gc)
    assert snap(gc).status == "available"  # the commit did land; every row had been verified first
    r = collect(gc, run="run-2", at=T0 + timedelta(hours=1))
    assert r.status == "unchanged" and len(events(gc, "commit")) == 1


# ── revisions ───────────────────────────────────────────────────────────

def test_h7_revision_preserves_earlier_snapshot(gc):
    first = collect(gc)
    second = collect(gc, revised(), run="run-2", at=T0 + timedelta(days=1))
    assert (second.status, second.revision) == ("committed", 2) and second.collection_id != first.collection_id
    s = snap(gc)
    assert s.collection_id == second.collection_id and s.rows[1]["total_gross"] == "59400000"
    assert s.superseded == [{"collection_id": first.collection_id, "revision": 1,
                             "last_retrieved_at_utc": T0.isoformat(timespec="seconds")}]
    old = H.collection_rows(H.read_log(gc, "S"), first.collection_id)
    assert len(old) == N and old[1]["total_gross"] == "58900000"  # the earlier snapshot is intact


def test_h8_crash_on_revision_then_source_reverts(gc):
    first = collect(gc)
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=2)
    with pytest.raises(Crash):
        collect(gc, revised(), run="run-2", at=T0 + timedelta(hours=12))
    assert snap(gc).collection_id == first.collection_id  # failed revision cannot hide the good snapshot
    back = collect(gc, run="run-3", at=T0 + timedelta(hours=20))
    assert back.status == "unchanged" and snap(gc).collection_id == first.collection_id
    later = collect(gc, run="run-4", at=T0 + timedelta(hours=70))
    assert len(later.orphans_aborted) == 1 and len(events(gc, "abort")) == 1
    assert physical_rows(gc) == N + 2  # orphaned rows stay, permanently invisible
    assert snap(gc).status == "available" and len(snap(gc).rows) == N


def test_latest_observation_wins_when_source_reverts_to_an_earlier_committed_version(gc):
    first = collect(gc)
    collect(gc, revised(), run="run-2", at=T0 + timedelta(days=1))
    collect(gc, run="run-3", at=T0 + timedelta(days=2))  # TN shows the original figures again
    s = snap(gc)
    assert s.collection_id == first.collection_id and s.superseded[0]["revision"] == 2


def test_revised_historical_totals_are_a_new_revision(gc):
    collect(gc)
    films = [list(f) for f in WEEKEND_FILMS]
    films[0][4], films[0][7] = 31_300_000, 31_300_000  # weekend actuals revised upward
    r = collect(gc, chart([tuple(f) for f in films]), run="run-2", at=TUE)
    assert r.status == "committed" and r.revision == 2
    assert snap(gc).rows[0]["weekend_gross"] == "31300000"


# ── H9: incomplete / invalid charts ─────────────────────────────────────

@pytest.mark.parametrize("bad", [
    lambda: chart(footer_total=56_025_001),                                     # malformed
    lambda: chart(WEEKEND_FILMS[:5], footer_count=8, footer_total=56_025_000),   # partial
    lambda: parse_chart(page("daily", date(2026, 10, 21)), weekend_url(FRI), "weekend", FRI),  # wrong chart
])
def test_h9_invalid_chart_writes_only_a_reject_event(gc, bad):
    r = collect(gc, bad())
    assert r.status == "rejected" and physical_rows(gc) == 0
    assert [e["event"] for e in events(gc)] == ["reject"]
    assert events(gc, "reject")[0]["chart_status"] in ("malformed", "partial", "wrong_chart")
    assert snap(gc).status == "missing"


def test_failed_new_collection_cannot_invalidate_an_earlier_snapshot(gc):
    good = collect(gc)
    collect(gc, chart(footer_total=1), run="run-2", at=T0 + timedelta(days=1))           # rejected
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=1)
    with pytest.raises(Crash):
        collect(gc, revised(), run="run-3", at=T0 + timedelta(days=2))                    # crashed revision
    s = snap(gc)
    assert (s.status, s.collection_id, len(s.rows)) == ("available", good.collection_id, N)


def test_duplicate_film_key_is_rejected(gc):
    films = list(WEEKEND_FILMS)
    films[7] = films[7][:3] + (films[6][3],) + films[7][4:]  # two rows share a TN slug
    r = collect(gc, chart(films))
    assert r.status == "rejected" and "duplicate_film_key" in r.detail and physical_rows(gc) == 0


def test_verification_failure_leaves_collection_uncommitted(gc, monkeypatch):
    real = H.read_log

    def tampered(*a, **k):
        log = real(*a, **k)
        if log.history:
            log.history[0] = dict(log.history[0], weekend_gross="1")  # read-back differs from the chart
        return log

    monkeypatch.setattr(H, "read_log", tampered)
    r = collect(gc)
    assert r.status == "unverified" and not events(gc, "commit")
    monkeypatch.setattr(H, "read_log", real)
    assert snap(gc).status == "missing"
    assert collect(gc, run="run-2", at=T0 + timedelta(hours=1)).status == "resumed_committed"


# ── H10 / H11 / H12 ─────────────────────────────────────────────────────

def test_h10_readers_ignore_uncommitted_rows(gc):
    log = H.read_log(gc, "S")
    plan = H.plan_collection(log, chart(), {}, "2026-10-18T14:00:00+00:00", "r", T0)
    tab(gc, H.HISTORY_TAB).rows += plan.rows  # rows without begin/commit
    assert snap(gc).status == "missing"
    assert H.collection_rows(H.read_log(gc, "S"), plan.collection_id) == []


def test_h11_concurrent_identical_runs(gc):
    log = H.read_log(gc, "S")
    a = H.plan_collection(log, chart(), {}, "2026-10-18T14:00:00+00:00", "run-A", T0)
    b = H.plan_collection(log, chart(), {}, "2026-10-18T14:00:05+00:00", "run-B", T0)
    assert a.collection_id == b.collection_id and [r[0] for r in a.rows] == [r[0] for r in b.rows]
    ra = H.execute(gc, "S", a, "run-A", "2026-10-18T14:00:00+00:00", T0)
    rb = H.execute(gc, "S", b, "run-B", "2026-10-18T14:00:05+00:00", T0)
    assert ra.status == "committed" and rb.status == "committed"
    assert physical_rows(gc) == 2 * N                   # physical duplicates...
    s = snap(gc)
    assert len(s.rows) == N and len(H.read_log(gc, "S").for_collection(a.collection_id, "commit")) == 1  # ...one logical commit


def test_h12_only_appends_ever_touch_history_tabs(gc):
    collect(gc)
    collect(gc, revised(), run="run-2", at=T0 + timedelta(days=1))
    collect(gc, chart(footer_total=1), run="run-3", at=T0 + timedelta(days=2))
    methods = {m for t, m, _ in gc.calls if t in (H.HISTORY_TAB, H.COLLECTIONS_TAB)}
    assert methods <= {"worksheet", "get_all_values", "append_rows"}  # open, read, append — nothing else
    src = open(H.__file__, encoding="utf-8").read()
    for forbidden in ("write_values", "update_cells", "create_tab", ".clear(", "batch_clear", "delete"):
        assert forbidden not in src.replace("never deleted", "").replace("Nothing is ever deleted", "")


# ── reads, gating, guard ────────────────────────────────────────────────

def test_disabled_by_default_reads_and_writes_nothing(gc, monkeypatch):
    monkeypatch.delenv("FARAMIR_COLLECT_HISTORY")
    before = len(gc.calls)
    assert collect(gc).status == "disabled" and len(gc.calls) == before


def test_failed_read_writes_nothing_and_is_not_missing(gc):
    gc.book("S").fail_worksheet(H.COLLECTIONS_TAB, make_api_error(503))
    assert collect(gc).status == "read_failed" and gc.writes() == []
    gc.book("S").fail_worksheet(H.COLLECTIONS_TAB, make_api_error(503))
    with pytest.raises(H.HistoryReadError):
        snap(gc)                                   # an error, never "no history"


def test_missing_tabs_are_never_created(monkeypatch):
    monkeypatch.setenv("FARAMIR_COLLECT_HISTORY", "true")
    gc = FakeClient()
    gc.book("S")
    r = H.collect(gc, "S", chart(), run_id="r", retrieved_at=T0, now=T0)
    assert r.status == "read_failed" and "migration" in r.detail
    assert not [c for c in gc.calls if c[1] == "add_worksheet"] and gc.writes() == []


def test_unexpected_header_is_a_read_error(gc):
    tab(gc, H.HISTORY_TAB).rows[0] = list(reversed(H.HISTORY_HEADERS))
    assert collect(gc).status == "read_failed" and gc.writes() == []


@pytest.mark.no_run_context
def test_writes_go_through_the_runtime_guard(gc, tmp_path):
    with pytest.raises(runtime.WriteBlocked):              # no run context at all
        collect(gc)
    ctx = runtime.RunContext(mode=runtime.DRY_RUN, enabled=False, run_id="dry", out_dir=str(tmp_path))
    with runtime.use(ctx):
        r = collect(gc)
    assert r.status == "dry_run" and gc.writes() == []
    assert [o["op"] for o in ctx.operations] == ["history_begin"] and ctx.operations[0]["outcome"] == "recorded"


# ── estimates vs actuals: policy status only ────────────────────────────

def test_single_retrieval_is_provisional(gc):
    collect(gc)
    assert snap(gc).collection_status == "provisional"


def test_two_retrievals_before_tuesday_stay_provisional(gc):
    collect(gc)
    collect(gc, run="run-2", at=T0 + timedelta(hours=20))   # Monday
    assert snap(gc).collection_status == "provisional"


def test_recollection_on_tuesday_confirms(gc):
    collect(gc)
    collect(gc, run="run-2", at=TUE)
    s = snap(gc)
    assert s.collection_status == "confirmed_by_recollection"
    assert [o["run_id"] for o in s.observations] == ["run-1", "run-2"]   # the retrieval evidence


def test_first_retrieval_on_tuesday_alone_is_not_confirmation(gc):
    collect(gc, at=TUE)
    assert snap(gc).collection_status == "provisional"


def test_revision_after_confirmation_restarts_as_provisional(gc):
    collect(gc)
    collect(gc, run="run-2", at=TUE)
    collect(gc, revised(), run="run-3", at=TUE + timedelta(days=1))
    assert snap(gc).collection_status == "provisional"


# ── missing weekends are unknown, never a decline/exit/broken streak ────

def _weekends(n):
    return [(FRI - timedelta(weeks=i)).isoformat() for i in range(n)]


def test_missing_weekend_is_unknown_not_an_exit(gc):
    collect(gc)                                                        # only 10-16 collected
    series = H.film_series(gc, "S", "Iron-Harbor-(2026)", _weekends(2))
    assert [e["state"] for e in series] == ["charted", "unknown"]
    assert H.streak(series, lambda r: r["source_rank"] == "1") == {"length": 1, "status": "indeterminate", "at": "2026-10-09"}


def test_not_on_chart_only_from_a_complete_committed_chart(gc):
    films = [(str(i + 1) if f[0] != "-" else "-",) + f[1:] for i, f in enumerate(WEEKEND_FILMS[1:])]
    assert collect(gc, chart(films)).status == "committed"            # Iron Harbor absent from a complete chart
    series = H.film_series(gc, "S", "Iron-Harbor-(2026)", _weekends(1))
    assert series[0]["state"] == "not_on_chart"
    assert H.streak(series, lambda r: True)["status"] == "broken"


def test_uncommitted_weekend_is_unknown(gc):
    tab(gc, H.HISTORY_TAB).fail_on("append_rows", Crash(), partial=3)
    with pytest.raises(Crash):
        collect(gc)
    assert H.film_series(gc, "S", "Iron-Harbor-(2026)", _weekends(1))[0]["state"] == "unknown"


# ── collection is independent of editorial eligibility ──────────────────

def test_every_valid_row_is_retained_whatever_its_eligibility(gc):
    identities = {"Quiet-Orchard-(2026)": {"tmdb_id": 9, "imdb_id": "tt9", "identity_confidence": "legacy",
                                           "identity_method": "state_legacy_title_year"}}
    collect(gc, identities=identities)
    rows = {r["source_film_key"]: r for r in snap(gc).rows}
    assert len(rows) == N  # nothing filtered by the 90-day rule or the $20M floor
    galaxy = rows["Galaxy-Run-(2019)"]                        # re-release, 2,733 days
    assert (galaxy["engagement"], galaxy["days_in_release"], galaxy["total_gross_scope"], galaxy["engagement_start"]) == (
        "rerelease", "2733", "all_engagements", "")
    assert (galaxy["source_film_key"], galaxy["slug_year"], galaxy["first_us_release"]) == ("Galaxy-Run-(2019)", "2019", "2019-04-26")
    assert rows["Old-Reel-(1985)"]["engagement"] == "rerelease" and rows["Old-Reel-(1985)"]["days_in_release"] == "15086"
    assert rows["Voyage-Le-(2024-France)"]["engagement"] == "late_release"
    assert rows["Small-Indie-(2026)"]["weekend_gross"] == "20000"   # far below Faramir's floors
    # Identity: only by slug, class preserved; unmatched stays unmatched.
    assert (rows["Quiet-Orchard-(2026)"]["identity_confidence"], rows["Quiet-Orchard-(2026)"]["tmdb_id"]) == ("legacy", "9")
    assert rows["Iron-Harbor-(2026)"]["identity_confidence"] == "unmatched" and rows["Iron-Harbor-(2026)"]["tmdb_id"] == ""


def test_reported_and_derived_ranks_and_source_evidence_are_kept(gc):
    collect(gc)
    s = snap(gc)
    r = s.rows[5]  # TN prints "-"
    assert (r["source_rank"], r["source_rank_text"], r["derived_rank"], r["derived_rank_basis"]) == ("", "-", "6", "faramir_gross_desc")
    first = s.rows[0]
    assert first["pct_change_reported"] == "" and first["prev_status"] == "new"   # unavailable: blank, not 0
    assert (first["source_url"], first["weekend_start"], first["weekend_end"]) == (weekend_url(FRI), "2026-10-16", "2026-10-18")
    assert first["retrieved_at_utc"] == "2026-10-18T14:00:00+00:00" and first["parser_version"] == "tn-chart-1"
    begin = events(gc, "begin")[0]
    assert (begin["content_hash"], begin["http_status"], begin["source_url"]) == (chart().content_hash(), "200", weekend_url(FRI))


def test_ids_depend_on_content_not_time_or_run(gc):
    a = H.collection_id(chart())
    assert a == H.collection_id(parse_chart(page(), "https://other", "weekend", FRI))
    assert a != H.collection_id(revised())
    assert H.record_id(a, "X") == H.record_id(a, "X") != H.record_id(a, "Y")


def test_concurrent_runs_with_different_content_lose_nothing(gc):
    # Two runs race on one weekend while TN revises it (the workflow concurrency group, PR-1, should
    # prevent this). Both commit; neither is lost; readers pick the most recently retrieved content.
    log = H.read_log(gc, "S")
    a = H.plan_collection(log, chart(), {}, "2026-10-18T14:00:00+00:00", "run-A", T0)
    b = H.plan_collection(log, revised(), {}, "2026-10-18T14:00:09+00:00", "run-B", T0)
    assert a.revision == b.revision == 1  # both planned against the same empty log
    H.execute(gc, "S", a, "run-A", "2026-10-18T14:00:00+00:00", T0)
    H.execute(gc, "S", b, "run-B", "2026-10-18T14:00:09+00:00", T0)
    s = snap(gc)
    assert s.collection_id == b.collection_id and len(s.rows) == N
    assert [x["collection_id"] for x in s.superseded] == [a.collection_id]
    assert len(H.collection_rows(H.read_log(gc, "S"), a.collection_id)) == N


# ── Increment 12: id scope and deterministic version selection ──────────

def test_identical_figures_on_different_weekends_never_share_ids(gc):
    nxt = date(2026, 10, 23)
    a = chart()
    b = parse_chart(page("weekend", nxt), weekend_url(nxt), "weekend", nxt)  # same rows, next weekend
    assert [r.gross for r in a.rows] == [r.gross for r in b.rows]
    assert H.collection_id(a) != H.collection_id(b)
    assert H.record_id(H.collection_id(a), "Iron-Harbor-(2026)") != H.record_id(H.collection_id(b), "Iron-Harbor-(2026)")
    collect(gc, a)
    collect(gc, b, run="run-2", at=T0 + timedelta(days=7))
    assert snap(gc, "2026-10-16").collection_id == H.collection_id(a)
    assert snap(gc, "2026-10-23").collection_id == H.collection_id(b)
    assert len(snap(gc, "2026-10-16").rows) == len(snap(gc, "2026-10-23").rows) == N


def test_collection_id_names_the_chart_type(gc):
    c = chart()
    assert H.collection_id(c) == H._sha(H.SOURCE, "weekend", c.weekend_start, c.content_hash())


def _race(gc, first, second):
    """Plan both runs against the same log, then execute in the given order."""
    log = H.read_log(gc, "S")
    plans = {name: (H.plan_collection(log, ch, {}, at, name, T0), at) for name, ch, at in (first, second)}
    for name, _, _ in (first, second):
        plan, at = plans[name]
        H.execute(gc, "S", plan, name, at, T0)
    return {name: plans[name][0].collection_id for name in plans}


def test_reader_uses_retrieval_time_not_commit_order(gc):
    # run-late retrieved the revised page later but its commit lands FIRST; run-early commits last.
    ids = _race(gc, ("run-late", revised(), "2026-10-18T14:00:30+00:00"),
                    ("run-early", chart(), "2026-10-18T14:00:05+00:00"))
    assert snap(gc).collection_id == ids["run-late"]


def test_reader_ignores_physical_row_order(gc):
    ids = _race(gc, ("run-early", chart(), "2026-10-18T14:00:05+00:00"),
                    ("run-late", revised(), "2026-10-18T14:00:30+00:00"))
    ws = tab(gc, H.COLLECTIONS_TAB)
    ws.rows = [ws.rows[0]] + list(reversed(ws.rows[1:]))        # shuffle the event log
    hs = tab(gc, H.HISTORY_TAB)
    hs.rows = [hs.rows[0]] + list(reversed(hs.rows[1:]))
    s = snap(gc)
    assert s.collection_id == ids["run-late"] and [int(r["position"]) for r in s.rows] == list(range(1, N + 1))


def test_retrieval_times_with_different_offsets_compare_correctly(gc):
    # 10:00:30-04:00 is 14:00:30Z: later than 14:00:05Z although it sorts earlier as text.
    ids = _race(gc, ("run-a", chart(), "2026-10-18T14:00:05+00:00"),
                    ("run-b", revised(), "2026-10-18T10:00:30-04:00"))
    assert snap(gc).collection_id == ids["run-b"]


def test_simultaneous_retrieval_of_different_versions_is_reported_not_guessed(gc):
    _race(gc, ("run-a", chart(), "2026-10-18T14:00:05+00:00"), ("run-b", revised(), "2026-10-18T14:00:05+00:00"))
    s = snap(gc)
    assert s.status == "conflicting_versions" and s.rows == []
    assert H.film_series(gc, "S", "Iron-Harbor-(2026)", ["2026-10-16"])[0]["state"] == "unknown"
    collect(gc, revised(), run="run-c", at=T0 + timedelta(hours=2))   # a later retrieval decides
    assert snap(gc).status == "available" and snap(gc).rows[1]["total_gross"] == "59400000"
