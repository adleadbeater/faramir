"""Phase 5: Streak, Legs, Exit, Dark Horse, Star Record, Global Split, Studio Battle, Premium Run,
overlap rules 3–5, wording rules, and the registry accounting for all 13 strategies + Discovery."""

from datetime import date, timedelta

import pytest

from faramir.classify import classify
from faramir.strategies import dark_horse, exit as exit_s, global_split, legs, premium_run, registry, star_record
from faramir.strategies import streak, studio_battle
from faramir.strategies.weekend import Weekend, WeekendRow
from tests.test_matcher import make_corpus_film, make_film

FRI = date(2026, 10, 16)


def R(key, rank, gross, theaters, days, prev="(1)", total=None, tmdb=None):
    return WeekendRow(key, key, rank, rank, gross, theaters, total or gross * 3, days, prev, "original", tmdb, None, "unmatched")


def W(i, rows):
    fri = FRI - timedelta(weeks=i)
    return Weekend(fri.isoformat(), f"c{i}", "confirmed_by_recollection", "u", rows)


def series(*weeks_rows):
    """weeks_rows oldest → newest."""
    n = len(weeks_rows)
    return [W(n - 1 - i, rows) if rows is not None else None for i, rows in enumerate(weeks_rows)]


# ── Streak ──────────────────────────────────────────────────────────────

def test_streak_fires_at_the_minimum_and_noteworthy_extensions():
    s = series(*[[R("A", 1, 10e6, 4000, 3 + 7 * i)] for i in range(3)])
    (e,) = streak.detect(s).events
    assert (e.subtype, e.evidence["weekends"], e.target["value"]) == ("top1", 3, "top1:3")
    s4 = series(*[[R("A", 1, 10e6, 4000, 3 + 7 * i)] for i in range(4)])
    assert streak.detect(s4).rejected[0]["reason_code"] == "not_a_noteworthy_extension"
    s5 = series(*[[R("A", 1, 10e6, 4000, 3 + 7 * i)] for i in range(5)])
    assert streak.detect(s5).events[0].evidence["weekends"] == 5


def test_streak_negative_cases():
    gap = series([R("A", 1, 1, 1, 3)], [R("A", 1, 1, 1, 10)], [R("B", 1, 1, 1, 3), R("A", 6, 1, 1, 17)], [R("A", 1, 1, 1, 24)])
    assert streak.detect(gap).events == []                                     # a weekend outside the tier breaks it
    missing = series([R("A", 1, 1, 1, 3)], None, [R("A", 1, 1, 1, 17)])
    r = streak.detect(missing)
    assert r.events == [] and r.rejected[0]["reason_code"] == "unverifiable_gap"   # never assumed to continue
    assert streak.detect(series([R("A", 1, 1, 1, 3)], [R("A", 1, 1, 1, 10)])).events == []   # N below minimum


def test_streak_top_three_tier():
    s = series(*[[R("A", r, 5e6, 3000, 3 + 7 * i)] for i, r in enumerate([2, 3, 1, 2])])
    (e,) = streak.detect(s).events
    assert e.subtype == "top3" and e.evidence["weekends"] == 4


# ── Exit ────────────────────────────────────────────────────────────────

def test_exit_theater_drop_and_small_base():
    s = series([R("A", 3, 5e6, 3000, 24), R("B", 9, 1e5, 500, 30)], [R("A", 5, 2e6, 1500, 31), R("B", 12, 4e4, 200, 37)])
    r = exit_s.detect(s)
    (e,) = r.events
    assert (e.film["title"], e.evidence["drop_pct"], e.subtype) == ("A", 50.0, "theater_drop")
    assert r.rejected[0]["reason_code"] == "small_base"


def test_end_of_run_needs_two_complete_absent_charts_and_a_stalled_total():
    on = lambda total: [R("A", 9, 1e5, 300, 80, total=total)]  # noqa: E731
    s = series(on(40_000_000), on(40_100_000), [R("X", 1, 1, 1, 3)], [R("X", 1, 1, 1, 10)])
    (e,) = [x for x in exit_s.detect(s).events if x.subtype == "end_of_run"]
    assert e.evidence["absent_from"] == [s[-2].weekend_start, s[-1].weekend_start]
    still_growing = series(on(30_000_000), on(40_000_000), [R("X", 1, 1, 1, 3)], [R("X", 1, 1, 1, 10)])
    assert not [x for x in exit_s.detect(still_growing).events if x.subtype == "end_of_run"]
    with_gap = series(on(40_000_000), on(40_100_000), None, [R("X", 1, 1, 1, 10)])
    assert not [x for x in exit_s.detect(with_gap).events if x.subtype == "end_of_run"]   # a missing chart is not an absence


# ── Legs ────────────────────────────────────────────────────────────────

def _legs_history(n_films=25):
    weeks = []
    for wk in range(2):
        rows = [R(f"F{i}", None, (10e6 if wk == 0 else 10e6 * (0.40 + i * 0.002)), 3000, 3 + 7 * wk,
                  prev="new" if wk == 0 else "(1)", total=(10e6 if wk == 0 else 10e6 * (1.40 + i * 0.002)))
                for i in range(n_films)]
        weeks.append(rows)
    return weeks


def test_legs_fires_for_a_top_decile_hold():
    h = _legs_history()
    hist = [Weekend((FRI - timedelta(weeks=20 - i)).isoformat(), f"h{i}", "confirmed_by_recollection", "u", rows)
            for i, rows in enumerate(h)]
    cur = series([R("Star", 2, 10e6, 3000, 3, prev="new", total=10e6)], [R("Star", 2, 8e6, 3050, 10, total=18e6)])
    (e,) = legs.detect(cur, hist).events
    assert e.evidence["weekend_change_pct"] == -20.0 and e.evidence["cohort_n"] >= 20 and "top_decile_hold" in e.reason_codes


def test_legs_negative_cases():
    small = [Weekend((FRI - timedelta(weeks=20 - i)).isoformat(), f"h{i}", "c", "u", rows) for i, rows in enumerate(_legs_history(5))]
    cur = series([R("Star", 2, 10e6, 3000, 3, prev="new", total=10e6)], [R("Star", 2, 8e6, 3050, 10, total=18e6)])
    assert legs.detect(cur, small).rejected[0]["reason_code"] == "cohort_too_small"
    expanding = series([R("Star", 2, 10e6, 1000, 3, prev="new")], [R("Star", 2, 12e6, 2500, 10)])
    assert legs.detect(expanding, []).rejected[0]["reason_code"] == "expansion_driven"
    thanksgiving = date(2026, 11, 27)
    hol = [Weekend((thanksgiving - timedelta(weeks=1)).isoformat(), "a", "c", "u", [R("S", 1, 1e6, 1000, 3, prev="new")]),
           Weekend(thanksgiving.isoformat(), "b", "c", "u", [R("S", 1, 2e6, 1000, 10)])]
    assert legs.detect(hol, []).rejected[0]["reason_code"] == "holiday_weekend"


# ── Dark Horse ──────────────────────────────────────────────────────────

def test_dark_horse_requires_a_verified_scale_advantage_at_the_same_stage():
    s = series([R("Small", 3, 9e6, 1800, 10, tmdb=1), R("Big", 4, 8e6, 4000, 10, tmdb=2)])
    (e,) = dark_horse.detect(s).events
    assert e.evidence["rival"] == "Big" and "4,000 vs 1,800 theaters" in e.evidence["scale_advantage"][0]
    assert "promot" not in e.reason
    franchise = series([R("Orig", 2, 9e6, 3500, 10, tmdb=1), R("Sequel", 3, 8e6, 3600, 3, tmdb=2)])
    (e,) = dark_horse.detect(franchise, {2: {"collection_id": 99}, 1: {}}).events
    assert "a franchise film against an original" in e.evidence["scale_advantage"]


@pytest.mark.parametrize("rows, meta", [
    ([R("Small", 3, 9e6, 1800, 3), R("Big", 4, 8e6, 4000, 24)], {}),                    # different stages
    ([R("Small", 3, 9e6, 3000, 10), R("Big", 4, 8e6, 3300, 10)], {}),                   # similar theaters, no budgets
    ([R("Small", 6, 9e6, 1800, 10), R("Big", 7, 8e6, 4000, 10)], {}),                   # F outside the top 5
])
def test_dark_horse_negative_cases(rows, meta):
    assert dark_horse.detect(series(rows), meta).events == []


# ── Star Record ─────────────────────────────────────────────────────────

def _active(**kw):
    fields = dict(tmdb_id=1, imdb_id="tt1", title="New One", canonical_title="New One", director="Ana Ruiz",
                  lead_cast=["Lead A", "Lead B", "Lead C", "Cameo D"], domestic_yesterday=140e6, domestic_today=150e6,
                  worldwide_yesterday=0, worldwide_today=0, dom_stale_days=0, ww_stale_days=0)
    fields.update(kw)
    return make_film(**fields)


def _corpus():
    floor = make_corpus_film(tmdb_id=900, title="Floor", domestic_lifetime=90_000_000, worldwide_lifetime=200_000_000,
                             domestic_status="reported", worldwide_status="reported", director="X", lead_cast=["Y"])
    prev_best = make_corpus_film(tmdb_id=10, title="Earlier Hit", director="Ana Ruiz", lead_cast=["Someone"],
                                 domestic_lifetime=145_000_000, worldwide_lifetime=300_000_000,
                                 domestic_status="reported", worldwide_status="reported")
    return [floor, prev_best]


def test_star_record_career_best_above_the_floor():
    (e,) = [x for x in star_record.detect(_active(), _corpus(), [], "2026-10-21", "2026-10-01").events
            if x.evidence["person"] == "Ana Ruiz"]
    assert (e.subtype, e.evidence["previous_best"], e.evidence["crossed_today"]) == ("director", "Earlier Hit", True)
    assert "career" in e.reason and e.evidence["previous_best_tmdb"] == 10


def test_star_record_negative_cases():
    below = _active(domestic_yesterday=60e6, domestic_today=85e6)
    # The person's only prior film is tracked (not in the corpus), so the floor stays at $90M.
    corpus = _corpus()[:1]
    prior = [{"tmdb_id": 77, "canonical_title": "Small Earlier", "director": "Ana Ruiz", "lead_cast": [],
              "domestic_today": 50e6}]
    r = star_record.detect(below, corpus, prior, "2026-10-21", None)
    assert r.events == [] and r.rejected[0]["reason_code"] == "unverifiable"            # below the corpus floor
    cameo = _active(director="Someone Else", lead_cast=["Lead A", "Lead B", "Lead C", "Ana Ruiz"])
    assert star_record.detect(cameo, _corpus(), [], "2026-10-21", None).events == []    # 4th billing: a cameo
    assert star_record.detect(_active(), _corpus(), [], "2026-10-21", None, corpus_ok=False).events == []


def test_star_record_counts_tracked_films_too():
    tracked = [{"tmdb_id": 77, "canonical_title": "Other 2026 Film", "director": "Ana Ruiz", "lead_cast": [],
                "domestic_today": 200e6}]
    assert [x for x in star_record.detect(_active(), _corpus(), tracked, "2026-10-21", None).events
            if x.evidence["person"] == "Ana Ruiz"] == []


# ── Global Split ────────────────────────────────────────────────────────

def _genre_corpus(n=16, share=0.5):
    return [make_corpus_film(tmdb_id=100 + i, genres=["Drama"], domestic_lifetime=100_000_000,
                             worldwide_lifetime=int(100_000_000 / (1 - share)), domestic_status="reported",
                             worldwide_status="reported") for i in range(n)]


def test_global_split_is_unverifiable_without_rollout_status():
    f = make_film(tmdb_id=1, imdb_id="tt1", genres=["Drama"], domestic_today=50e6, worldwide_today=500e6)
    (e,) = global_split.detect(f, _genre_corpus(), "2026-10-21", None).events
    assert e.status == "unverifiable" and e.evidence["revision_risk"] and "rollout_unknown" in e.reason_codes
    (e,) = global_split.detect(f, _genre_corpus(), "2026-10-21", None, rollout_complete=True).events
    assert e.status == "confirmed"


def test_global_split_negative_cases():
    normal = make_film(tmdb_id=1, imdb_id="tt1", genres=["Drama"], domestic_today=100e6, worldwide_today=200e6)
    assert global_split.detect(normal, _genre_corpus(), "d", None).rejected[0]["reason_code"] == "normal_for_genre"
    assert global_split.detect(normal, _genre_corpus(5), "d", None).rejected[0]["reason_code"] == "cohort_too_small"


# ── Studio Battle and Premium Run (no source: interface only) ───────────

def test_studio_battle_on_synthetic_ytd_with_name_variants():
    prev = [{"distributor": "Walt Disney", "ytd_gross": 1.8e9}, {"distributor": "Warner Bros.", "ytd_gross": 1.9e9},
            {"distributor": "Universal", "ytd_gross": 1.0e9}]
    cur = [{"distributor": "Disney", "ytd_gross": 1.5e9}, {"distributor": "Buena Vista", "ytd_gross": 0.6e9},
           {"distributor": "Warner Bros", "ytd_gross": 1.95e9}, {"distributor": "Universal", "ytd_gross": 1.05e9}]
    events = studio_battle.detect(cur, prev, "2026-10-16", "synthetic").events
    disney = next(e for e in events if e.target["id"] == "Disney")
    assert disney.evidence["rank"] == 1 and disney.evidence["ytd_gross"] == 2.1e9    # name variants merged
    assert studio_battle.detect(cur, prev, "d", complete=False).events[0].status == "unverifiable"
    assert studio_battle.detect(None, None, "d").rejected[0]["reason_code"] == "blocked_no_source"


def test_premium_run_refuses_without_a_verified_source():
    r = premium_run.detect({"imax_screens": 400})
    assert r.events == [] and r.rejected[0]["reason_code"] == "blocked_no_source"


# ── overlap rules 3, 4, 5 ───────────────────────────────────────────────

from faramir.events.schema import Event, Source  # noqa: E402


def ev(strategy, target, strength=0.7, evidence=None, axis="weekend", period="2026-10-16"):
    return Event(strategy=strategy, film={"tmdb_id": 1, "title": "A", "tn_slug": "A"}, period=period, axis=axis,
                 target=target, status="confirmed", evidence=evidence or {"x": 1},
                 sources=[Source("tn_weekend_history", "c", period)], reason="r", reason_codes=["c"], strength=strength)


def test_rule_3_hold_over_streak_with_legs_secondary():
    hold = ev("hold", {"type": "rank", "value": 1}, 0.7)
    stk = ev("streak", {"type": "streak", "value": "top1:3"}, 0.9)
    lg = ev("legs", {"type": "retention", "value": 3}, 0.65)
    (c,) = classify([stk, lg, hold])
    assert (c.primary.strategy, c.secondary.strategy) == ("hold", "legs")
    assert c.rejected_alternatives == [{"event_id": stk.event_id, "strategy": "streak", "reason_code": "overlap_rule_3:hold_over_streak"}]
    (c,) = classify([stk, lg])                     # no challenger: Streak primary, Legs secondary
    assert (c.primary.strategy, c.secondary.strategy) == ("streak", "legs")
    (c,) = classify([lg])
    assert c.primary.strategy == "legs"            # no rank condition: Legs primary


def test_rule_4_takeover_beats_dark_horse_which_is_secondary():
    (c,) = classify([ev("dark_horse", {"type": "film", "id": "B"}, 0.9), ev("takeover", {"type": "rank", "value": 1}, 0.8)])
    assert (c.primary.strategy, c.secondary.strategy) == ("takeover", "dark_horse")


def test_rule_5_star_record_beats_overtake_of_the_talents_own_film():
    sr = ev("star_record", {"type": "person", "id": "Ana Ruiz"}, 0.85, {"previous_best_tmdb": 10}, axis="domestic")
    ov = ev("overtake", {"type": "comp", "tmdb_id": 10, "imdb_id": "tt10"}, 0.9, axis="domestic")
    other = ev("overtake", {"type": "comp", "tmdb_id": 11, "imdb_id": "tt11"}, 0.9, axis="domestic")
    clusters = classify([sr, ov, other])
    merged = next(c for c in clusters if c.primary.strategy == "star_record")
    assert merged.rejected_alternatives[0]["reason_code"] == "star_record_beats_overtake" and merged.secondary is None
    assert any(c.primary.event_id == other.event_id for c in clusters)   # an unrelated overtake stays its own story


# ── wording rules ───────────────────────────────────────────────────────

from faramir.validate import lexical_checks  # noqa: E402


def _p(headline, strategy=None, rank=None):
    ev_ = {"event": {"axis": "weekend", "kind": "event", "milestone_type": None, "threshold": None, "status": "confirmed"},
           "active": {"title": "New Hit", "genres": []}, "comp": None, "categories": []}
    if strategy:
        ev_["strategy_event"] = {"strategy": strategy, "chart_rank": rank}
    return {"active_title": "New Hit", "headline": headline, "angle": "", "claims": [], "evidence": ev_}


def test_third_straight_weekend_is_ordinary_language():
    assert lexical_checks(_p("New Hit Stays No. 1 for a Third Straight Weekend", "streak", 1), []) == []


@pytest.mark.parametrize("headline, strategy, ok", [
    ("New Hit Is Now Ana Ruiz's Biggest Career Hit", "star_record", True),
    ("New Hit Becomes the Career-Best Film for Ana Ruiz", "star_record", True),
    ("New Hit Is Now Ana Ruiz's Biggest Career Hit", "overtake", False),     # no Star Record evidence
    ("New Hit Is the Biggest Film of the Year", "star_record", False),        # not a career claim
])
def test_career_record_wording_only_with_star_record_evidence(headline, strategy, ok):
    reasons = [r for r in lexical_checks(_p(headline, strategy), []) if r.startswith("lexical_superlative")]
    assert (reasons == []) is ok


# ── registry audit: all 13 strategies + Discovery ───────────────────────

def test_registry_final_audit():
    reg = registry()
    s = reg["strategies"]
    assert len(s) == 13 and sorted(v["number"] for v in s.values()) == list(range(1, 14))
    for name, v in s.items():
        if v["data_status"] in ("blocked_no_source", "blocked_permission", "synthetic_only"):
            assert not v["enabled"], name                      # never enabled without its source
    assert {k for k, v in s.items() if v["enabled"]} == {"overtake", "chase", "countdown", "takeover", "hold", "streak",
                                                         "exit", "legs", "dark_horse"}
    assert reg["discovery"]["enabled"] and reg["discovery"]["data_status"] == "history_required"
    for name in ("streak", "legs", "exit", "dark_horse", "star_record", "global_split", "studio_battle", "premium_run"):
        assert s[name].get("note"), name


def test_disabled_daily_strategies_do_not_run(tmp_path):
    from faramir.events.ledger import Ledger
    from faramir import strategy_engine

    f = make_film(tmdb_id=1, imdb_id="tt1", title="X", genres=["Drama"], domestic_yesterday=1, domestic_today=50e6,
                  worldwide_yesterday=1, worldwide_today=500e6, dom_stale_days=0, ww_stale_days=0)
    r = strategy_engine.run_daily([f], [{"tmdb_id": 1, "candidates": []}], _genre_corpus(), "2026-10-21", None, set(),
                                  Ledger(str(tmp_path)))
    assert not [e for e in r.events if e.strategy in ("global_split", "star_record")]
