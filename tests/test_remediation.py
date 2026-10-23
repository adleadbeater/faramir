"""Phase 0 acceptance remediation: regression tests for the defects found in
the independent review (axis-specific figures, named talent, ambiguous
identity overrides, unconfirmed Slack deliveries, history row integrity)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from faramir.validate import figure_checks, lexical_checks, validate_pick

# ── shared evidence: the reviewer's reproduction ────────────────────────

ALPHA = {"tmdb_id": 1, "title": "Alpha Film", "genres": ["Drama"], "director": "Dana Director",
         "lead_cast": ["Lead One", "Lead Two"], "domestic_today": 120_000_000, "worldwide_today": 400_000_000}
BETA = {"tmdb_id": 2, "title": "Beta Story", "genres": ["Drama"], "director": "Ana Ruiz", "lead_cast": ["Sam Hale"],
        "is_best_picture": False, "domestic_lifetime": 100_000_000, "domestic_status": "reported",
        "worldwide_lifetime": 300_000_000, "worldwide_status": "reported", "identity_status": "verified"}


def pick(headline, angle="", axis="domestic", comp=BETA, active=ALPHA, kind="just_passed", threshold=None, mtype=None):
    claim = ({"type": "passed_comp", "comp_tmdb_id": 2, "axis": axis} if kind == "just_passed" else
             {"type": "crossed_milestone", "threshold": threshold, "axis": axis})
    claim = {"comp_tmdb_id": None, "threshold": None, "relation": None, "genre": None, "text": "x", **claim}
    return {"active_title": active["title"], "comp_title": (comp or {}).get("title"), "axis": axis, "kind": kind,
            "headline": headline, "angle": angle, "claims": [claim],
            "evidence": {"event": {"axis": axis, "kind": kind, "milestone_type": mtype, "threshold": threshold},
                         "active": dict(active), "comp": None if comp is None else dict(comp), "categories": []}}


# ── 1. axis-specific factual validation ─────────────────────────────────

def test_reviewer_reproduction_is_rejected():
    v = validate_pick(pick("Alpha Film Passes Beta Story With $400M Domestic"), [])
    assert not v.ok
    assert v.reasons == ["figure_misattributed: $400M labelled domestic matches no domestic evidence "
                         "($400M is Alpha Film worldwide)"]


@pytest.mark.parametrize("headline, angle", [
    ("Alpha Film ($120M) Passes Beta Story ($100M) Domestically", ""),
    ("Alpha Film Passes Beta Story Domestically With $400M Worldwide", ""),
    ("Alpha Film Passes Beta Story", "Its domestic total of $120M tops Beta Story's $100M."),
    ("Alpha Film Passes Beta Story", "Worldwide, it has $400 million; Beta Story finished with $300M worldwide."),
    ("Alpha Film Passes Beta Story", "It now leads by $20M domestically."),
    ("Alpha Film Passes Beta Story", "Overseas it has taken $280M."),                # international = 400 - 120
    ("Alpha Film Passes Beta Story", "Beta Story's $200M international haul is still ahead."),  # 300 - 100
    ("Alpha Film Passes Beta Story", "It leads Beta Story by $100M worldwide."),       # worldwide gap
    ("Alpha Film Passes Beta Story's $100M Domestic Total", ""),
])
def test_legitimate_domestic_and_worldwide_figures_pass(headline, angle):
    assert figure_checks(pick(headline, angle)) == []


@pytest.mark.parametrize("headline, angle, reason", [
    ("Alpha Film Passes Beta Story With $400M Domestic", "", "figure_misattributed"),     # worldwide labelled domestic
    ("Alpha Film Passes Beta Story", "Beta Story made $300M domestically.", "figure_misattributed"),
    ("Alpha Film Passes Beta Story", "Alpha Film has $120M worldwide.", "figure_misattributed"),  # domestic as worldwide
    ("Alpha Film Passes Beta Story With $400M", "", "figure_axis_unlabelled"),            # unlabelled other-axis figure
    ("Alpha Film Passes Beta Story's $120M", "", "figure_misattributed"),                 # Alpha's figure on Beta
    ("Alpha Film ($100M) Passes Beta Story", "", "figure_misattributed"),                 # Beta's figure on Alpha
    ("Alpha Film Passes Beta Story", "It is $20M ahead worldwide.", "figure_misattributed"),  # domestic gap as worldwide
    ("Alpha Film Passes Beta Story", "It has $500M worldwide.", "unverified_figure"),
])
def test_figures_attributed_to_the_wrong_axis_or_film_are_rejected(headline, angle, reason):
    reasons = figure_checks(pick(headline, angle))
    assert reasons and reasons[0].startswith(reason), reasons


def test_worldwide_event_reads_unlabelled_figures_as_worldwide():
    ok = pick("Alpha Film Passes Beta Story's $300M", axis="worldwide")
    ok["claims"][0]["axis"] = "worldwide"
    assert figure_checks(ok) == []
    bad = pick("Alpha Film Passes Beta Story's $100M", axis="worldwide")
    assert any(r.startswith("figure_axis_unlabelled") for r in figure_checks(bad))


def test_milestone_threshold_is_tied_to_its_axis():
    ms = dict(comp=None, kind="milestone", threshold=400_000_000, mtype="just_passed", axis="worldwide")
    assert figure_checks(pick("Alpha Film Crosses $400 Million Worldwide", **ms)) == []
    assert figure_checks(pick("Alpha Film Crosses $400 Million", **ms)) == []          # unlabelled: event axis
    assert figure_checks(pick("Alpha Film Crosses $400 Million Domestic", **ms))      # wrong axis


# ── 2. named talent and filmmakers ──────────────────────────────────────

def test_reviewer_talent_reproduction_is_rejected():
    v = validate_pick(pick("Christopher Nolan's Alpha Film Passes Beta Story"), [])
    assert v.reasons == ["person_unsupported: 'Christopher Nolan' is not in the evidence for the active film's "
                         "director or cast"]


@pytest.mark.parametrize("headline, angle", [
    ("Dana Director's Alpha Film Passes Beta Story", ""),
    ("Director's Alpha Film Passes Beta Story", ""),                     # surname-only of "Dana Director"
    ("Alpha Film, Starring Lead One, Passes Beta Story", ""),
    ("Alpha Film Passes Ana Ruiz's Beta Story", ""),                     # comp director on the comp title
    ("Alpha Film Passes Beta Story", "The Lead Two-led film keeps climbing."),
    ("Alpha Film Passes Beta Story", "It now tops the film directed by Ana Ruiz."),
    # Title Case headlines without people: never flagged
    ("Alpha Film Passes Beta Story At The Halloween Weekend Box Office", ""),
    ("Alpha Film Passes Beta Story In Its Second Weekend", ""),
    ("Alpha Film's Domestic Run Passes Beta Story", ""),
    ("Alpha Film Passes Beta Story As Holiday Season Nears", ""),
])
def test_supported_or_absent_people_pass(headline, angle):
    assert [r for r in lexical_checks(pick(headline, angle), []) if r.startswith("person")] == []


@pytest.mark.parametrize("headline, angle, reason", [
    ("Christopher Nolan's Alpha Film Passes Beta Story", "", "person_unsupported"),
    ("Nolan's Alpha Film Passes Beta Story", "", "person_unsupported"),
    ("Ana Ruiz's Alpha Film Passes Beta Story", "", "person_misattributed"),     # comp's director on the active film
    ("Dana Director's Beta Story Is Passed By Alpha Film", "", "person_misattributed"),
    ("Alpha Film, Starring Tom Cruise, Passes Beta Story", "", "person_unsupported"),
    ("Alpha Film Passes Beta Story", "The Margot Robbie-led hit keeps climbing.", "person_unsupported"),
    ("Pixar's Alpha Film Passes Beta Story", "", "person_unsupported"),           # unsupported studio attribution
])
def test_unsupported_or_misattributed_people_are_rejected(headline, angle, reason):
    reasons = [r for r in lexical_checks(pick(headline, angle), []) if r.startswith("person")]
    assert reasons and reasons[0].startswith(reason), reasons


def test_comparison_credits_count_only_with_verified_identity():
    unverified = dict(BETA, identity_status="unresolved")
    reasons = lexical_checks(pick("Alpha Film Passes Ana Ruiz's Beta Story", comp=unverified), [])
    assert any(r.startswith("person_unsupported") for r in reasons)


def test_binding_evidence_carries_credits():
    from faramir.binding import bind_picks

    active = [{"tmdb_id": 1, "title": "Alpha Film", "director": "Dana Director", "lead_cast": ["Lead One"],
               "genres": ["Drama"], "domestic_today": 120_000_000, "worldwide_today": 400_000_000,
               "candidates": [{"kind": "just_passed", "axis": "domestic", "threshold_value": None,
                               "angle_categories": ["unexpected_giant_killer"], "suppressed": False,
                               "comp": dict(BETA, imdb_id="tt2", release_year="2010")}]}]
    p = {"active_tmdb_id": 1, "active_title": "Alpha Film", "comp_tmdb_id": 2, "comp_title": "Beta Story",
         "axis": "domestic", "kind": "just_passed", "threshold_value": None, "angle_category": "unexpected_giant_killer",
         "headline": "h", "angle": "a", "claims": []}
    (bound,) = bind_picks([p], active, "2026-10-22").accepted
    assert bound["evidence"]["active"]["director"] == "Dana Director"
    assert bound["evidence"]["comp"]["director"] == "Ana Ruiz" and bound["evidence"]["comp"]["lead_cast"] == ["Sam Hale"]


# ── 3. ambiguous identity overrides ─────────────────────────────────────

from faramir.identity import OverrideConflict, parse_overrides, resolve  # noqa: E402
from tests.test_identity import TMDB, entry, result  # noqa: E402
from tests.fakes.state_book import state_row  # noqa: E402

H = ["tn_slug", "tmdb_id", "note"]


def test_identical_duplicate_overrides_map_once_with_a_note():
    o = parse_overrides([H, ["New-Hit-(2026)", "777", "a"], ["New-Hit-(2026)", "777", "b"]])
    assert o.get("New-Hit-(2026)") == 777
    assert any("listed 2 times with the same tmdb_id 777" in p for p in o.problems)


def test_conflicting_duplicate_overrides_are_refused():
    o = parse_overrides([H, ["New-Hit-(2026)", "777", ""], ["Other-(2026)", "5", ""], ["New-Hit-(2026)", "778", ""]])
    c = o.get("New-Hit-(2026)")
    assert isinstance(c, OverrideConflict) and "conflicting tmdb_ids ['777', '778'] (rows 2, 4)" in c.reason
    assert o.get("Other-(2026)") == 5
    assert any("New-Hit-(2026) not used" in p for p in o.problems)


@pytest.mark.parametrize("rows, expect", [
    ([["New-Hit-(2026)", "tt1234", ""]], "malformed tmdb_id"),
    ([["New-Hit-(2026)", "777", ""], ["New-Hit-(2026)", "abc", ""]], "malformed tmdb_id"),   # good + bad row
    ([["New-Hit-(2026)", "", "forgot the id"]], "malformed tmdb_id"),
])
def test_malformed_overrides_are_refused(rows, expect):
    c = parse_overrides([H] + rows).get("New-Hit-(2026)")
    assert isinstance(c, OverrideConflict) and expect in c.reason


def test_rows_without_slug_and_bad_header_are_reported():
    o = parse_overrides([H, ["", "777", "orphan"]])
    assert o.by_slug == {} and "without a tn_slug" in o.problems[0]
    o = parse_overrides([["slug", "id"], ["New-Hit-(2026)", "777"]])
    assert o.by_slug == {} and "lacks tn_slug/tmdb_id" in o.problems[0]


def test_conflicting_override_leaves_the_film_unresolved_without_guessing():
    tmdb = TMDB([result(9, "New Hit", "2026-10-12")])
    o = parse_overrides([H, ["New-Hit-(2026)", "777", ""], ["New-Hit-(2026)", "778", ""]])
    ident = resolve(entry(), [state_row(5, "New Hit", tn_slug="New-Hit-(2026)")], o, tmdb.search, tmdb.details,
                    tmdb.release_dates)
    assert ident.status == "unresolved" and ident.reason.startswith("override_conflict")
    assert tmdb.calls == []  # neither override, nor the state row, nor a search is used


def test_malformed_state_override_cell_is_unresolved_not_ignored():
    tmdb = TMDB()
    ident = resolve(entry(), [state_row(5, "New Hit", tn_slug="New-Hit-(2026)", tmdb_id_override="tt0001")], {},
                    tmdb.search, tmdb.details, tmdb.release_dates)
    assert ident.status == "unresolved" and ident.reason.startswith("override_malformed") and tmdb.calls == []


from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, pick_first_candidate, pick_msgs, state_after  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_daily_run_reports_conflicting_overrides(env):
    env["gc"].book("S").add_tab("identity_overrides", [H, ["Alpha-Film-(2026)", "7001", ""],
                                                       ["Alpha-Film-(2026)", "7002", ""]])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    (a,) = [i for i in report["identity"] if i["title"] == "Alpha Film"]
    assert a["status"] == "unresolved" and a["reason"].startswith("override_conflict")
    assert any("Alpha-Film-(2026) not used" in p for p in report["identity_overrides"]["problems"])
    header = json.dumps(header_msgs(env["slack"])[0]["blocks"], ensure_ascii=False)
    assert "Alpha Film (override_conflict)" in header
    assert {7001, 7002}.isdisjoint({r["tmdb_id"] for r in state_after(env["gc"])})


def test_daily_run_drops_a_pick_naming_unsupported_talent(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)

    def respond(kwargs):
        data = json.loads(pick_first_candidate(kwargs))
        data["picks"][0]["headline"] = "Christopher Nolan's " + data["picks"][0]["headline"]
        return json.dumps(data)

    FakeAnthropic([respond]).install(env["monkeypatch"])
    assert main(env) == 0
    assert pick_msgs(env["slack"]) == []
    report, _, _ = artifacts(env)
    assert report["validation"][0]["reasons"][0].startswith("person_unsupported: 'Christopher Nolan'")


# ── 4. unconfirmed Slack deliveries ─────────────────────────────────────

from tests.test_delivery import NOW, T, item, log, reconcile_now, state_of, tagged_posts, world  # noqa: E402,F401
from tests.test_delivery import pick as dpick  # noqa: E402
from faramir.delivery import deliver, triage_undelivered  # noqa: E402
from faramir.suggestions import read_table  # noqa: E402


def _unconfirmed(world):
    gc, _, slack = world
    r = dpick(9)
    log(gc, [r])
    slack.fail_next_post(timeout=True, deliver=False)
    deliver(gc, "S", T, [item(r)], "run1", now_fn=lambda: NOW)
    assert reconcile_now(gc, now=NOW + timedelta(minutes=10)).changed == {r["suggestion_id"]: "unconfirmed"}
    return gc, slack, r


def _appears_in_history(slack, r):
    from faramir.slack_post import post_pick
    post_pick("xoxb", "C1", r, {}, None, suggestion_id=r["suggestion_id"], run_id="late")  # e.g. a delayed post


def test_unconfirmed_becomes_posted_when_history_finds_it(world):
    gc, slack, r = _unconfirmed(world)
    _appears_in_history(slack, r)
    rec = reconcile_now(gc, now=NOW + timedelta(hours=2))
    assert rec.changed == {r["suggestion_id"]: "posted"}
    row = read_table(gc, "S").by_id()[r["suggestion_id"]][0]
    assert row.get("slack_ts") == tagged_posts(slack, r["suggestion_id"])[0]
    assert "after being unconfirmed" in row.get("delivery_note")
    assert len(slack.messages) == 1  # found, never reposted


def test_unconfirmed_found_by_visible_ref_only(world):
    gc, slack, r = _unconfirmed(world)
    slack.metadata_readable = False
    _appears_in_history(slack, r)
    assert reconcile_now(gc, now=NOW + timedelta(hours=2)).changed == {r["suggestion_id"]: "posted"}


@pytest.mark.parametrize("failure", ["ratelimited", "timeout", "drop_cursor"])
def test_unconfirmed_unchanged_by_a_failed_or_incomplete_read(world, failure):
    gc, slack, r = _unconfirmed(world)
    _appears_in_history(slack, r)
    _appears_in_history(slack, dpick(10))  # a second message, so a 1-per-page read has a page to lose
    slack.page_size_cap = 1 if failure == "drop_cursor" else None
    slack.history_failures.append(failure)
    rec = reconcile_now(gc, now=NOW + timedelta(hours=2))
    assert r["suggestion_id"] not in rec.changed and state_of(gc, r["suggestion_id"]) == "unconfirmed"


def test_unconfirmed_still_absent_stays_unconfirmed_and_is_never_reposted(world):
    gc, slack, r = _unconfirmed(world)
    rec = reconcile_now(gc, now=NOW + timedelta(hours=2))
    assert rec.changed == {} and state_of(gc, r["suggestion_id"]) == "unconfirmed"
    assert triage_undelivered(read_table(gc, "S"), NOW.date(), NOW.date(), {"9"}).replay == []
    assert slack.messages == []


# ── 5. weekend-history row integrity ────────────────────────────────────

from faramir import history as HI  # noqa: E402
from tests.test_history import Crash, T0, chart, collect, events, physical_rows, snap, tab  # noqa: E402
from tests.test_history import gc as hgc  # noqa: E402,F401

N = 8


def _col(name):
    return HI.HISTORY_HEADERS.index(name)


def test_modified_gross_after_commit_is_detected_and_repaired(hgc):
    collect(hgc)
    ws = tab(hgc, HI.HISTORY_TAB)
    ws.rows[2][_col("weekend_gross")] = "99999999"          # someone edits a committed gross
    s = snap(hgc)
    assert s.status == "integrity_error" and s.rows == [] and "altered rows 1" in s.detail
    assert HI.film_series(hgc, "S", "Quiet-Orchard-(2026)", ["2026-10-16"])[0]["state"] == "unknown"
    r = collect(hgc, run="run-2", at=T0 + timedelta(hours=3))  # re-collect the same content: append-only repair
    assert (r.status, r.rows_written) == ("repaired", 1)
    s = snap(hgc)
    assert s.status == "available" and s.rows[1]["weekend_gross"] == "12400000"
    assert "ignored 0 partial, 1 altered" in s.detail
    assert physical_rows(hgc) == N + 1 and ws.rows[2][_col("weekend_gross")] == "99999999"  # nothing erased


def test_altered_row_with_a_valid_duplicate_is_ignored(hgc):
    collect(hgc)
    ws = tab(hgc, HI.HISTORY_TAB)
    bad = list(ws.rows[3])
    bad[_col("total_gross")] = "1"
    ws.rows.insert(1, bad)                                   # an altered copy BEFORE the valid one
    s = snap(hgc)
    assert s.status == "available" and s.rows[2]["total_gross"] == "6100000" and "1 altered" in s.detail


def test_partially_written_row_is_never_read_and_resume_rewrites_it(hgc):
    tab(hgc, HI.HISTORY_TAB).fail_on("append_rows", Crash(), partial=3)
    with pytest.raises(Crash):
        collect(hgc)
    ws = tab(hgc, HI.HISTORY_TAB)
    cut = _col("weekend_gross")
    ws.rows[3] = ws.rows[3][:cut] + [""] * (len(HI.HISTORY_HEADERS) - cut)   # third row cut off mid-write
    r = collect(hgc, run="run-2", at=T0 + timedelta(hours=1))
    assert (r.status, r.rows_written) == ("resumed_committed", N - 2)          # the partial record re-appended
    s = snap(hgc)
    assert s.status == "available" and len(s.rows) == N and "1 partial" in s.detail
    assert s.rows[2]["weekend_gross"] == "6100000"


def test_partial_row_after_commit_fails_safe_then_repairs(hgc):
    collect(hgc)
    ws = tab(hgc, HI.HISTORY_TAB)
    ws.rows[1] = ws.rows[1][:5] + [""] * (len(HI.HISTORY_HEADERS) - 5)
    assert snap(hgc).status == "integrity_error"
    assert collect(hgc, run="run-2", at=T0 + timedelta(hours=1)).status == "repaired"
    assert snap(hgc).status == "available"


def test_tampered_row_with_consistent_looking_values_is_still_rejected(hgc):
    collect(hgc)
    ws = tab(hgc, HI.HISTORY_TAB)
    ws.rows[1][_col("weekend_gross")] = "31250001"
    ws.rows[1][_col("theater_avg")] = "8682"                 # a careful edit; the committed hash still differs
    assert snap(hgc).status == "integrity_error"


def test_commit_without_record_hashes_is_an_integrity_error(hgc):
    collect(hgc)
    ws = tab(hgc, HI.COLLECTIONS_TAB)
    commit = next(r for r in ws.rows if r[HI.COLLECTION_HEADERS.index("event")] == "commit")
    commit[HI.COLLECTION_HEADERS.index("record_hashes")] = ""
    assert snap(hgc).status == "integrity_error"


def test_repair_only_appends(hgc):
    collect(hgc)
    tab(hgc, HI.HISTORY_TAB).rows[2][_col("weekend_gross")] = "1"
    collect(hgc, run="run-2", at=T0 + timedelta(hours=3))
    methods = {m for t, m, _ in hgc.calls if t in (HI.HISTORY_TAB, HI.COLLECTIONS_TAB)}
    assert methods <= {"worksheet", "get_all_values", "append_rows"}
    assert [e["event"] for e in events(hgc)] == ["begin", "commit", "observe"]
