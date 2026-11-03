"""Phase 4: age-comparable outcomes, shrinkage estimates, transparent ranker and exploration,
priority config proposals, explainer and performance report, the Aragorn refiner interface, and
generic event delivery (weekend events through Claude, binding, validation and Slack)."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from faramir.events.ledger import Ledger
from faramir.events.schema import Event, Source
from faramir.learning import outcomes, priority, rank as R
from faramir.learning.estimates import estimate
from faramir.reports import explainer, performance

CFG = priority.load()


# ── outcomes: only age-comparable, never extrapolated ───────────────────

def _obs(url, age, sessions, snap="2026-10-20"):
    return {"id": f"{url}|{snap}|{age}", "url": url, "snapshot_as_of": snap, "age_days": age,
            "cumulative_sessions": sessions, "discover_impressions": 2000, "discover_clicks": 10}


def test_outcome_uses_the_observation_closest_to_day_14_inside_the_window():
    obs = [_obs("u", 9, 3000), _obs("u", 16, 6000, "b"), _obs("u", 15, 5500, "a"), _obs("u", 40, 9000)]
    o = outcomes.at_age(obs, "u")
    assert (o.status, o.sessions, o.age_days, o.failure, o.heavy_hitter, o.discover_visible) == ("ok", 5500, 15, False, False, True)


@pytest.mark.parametrize("ages, status", [([3], "no_age_comparable_observation"), ([90], "no_age_comparable_observation"),
                                          ([], "no_observation")])
def test_no_outcome_without_an_age_comparable_observation(ages, status):
    assert outcomes.at_age([_obs("u", a, 1) for a in ages], "u").status == status


def test_observe_records_age_at_snapshot(tmp_path):
    from faramir.analytics import cerebro_snapshot as CS
    from tests.fixtures import cerebro_synth as SYN

    s = CS.load(SYN.write(tmp_path / "c.csv", [SYN.row("A Passes B", 5000, days_old=15, url="https://movieweb.com/x/")]),
                as_of=SYN.AS_OF)
    (row,) = outcomes.observe(s, {"https://movieweb.com/x/"})
    assert (row["age_days"], row["cumulative_sessions"], row["team"]) == (15, 5000, "news")


# ── estimates ───────────────────────────────────────────────────────────

def test_no_outcomes_means_calibration_pending():
    est = estimate([], "strategy")
    assert est["__global__"].status == "calibration_pending" and est["__global__"].estimate is None


def test_shrinkage_and_experimental_fallback():
    rows = [{"strategy": "overtake", "sessions": 8000.0} for _ in range(20)] + \
           [{"strategy": "chase", "sessions": 2000.0} for _ in range(20)] + [{"strategy": "countdown", "sessions": 20000.0}]
    est = estimate(rows, "strategy", k=10, min_n=8)
    g = est["__global__"].estimate
    o = est["overtake"]
    assert not o.is_experimental and g < o.estimate < 8000 and o.interval[0] < o.estimate < o.interval[1]
    c = est["countdown"]
    assert c.is_experimental and c.estimate == g and c.fallback_level == "global"   # n=1 uses the broader level


def test_strategy_and_construction_are_separate_factors():
    rows = [{"strategy": "overtake", "construction": "passes_comparison", "sessions": 9000.0}] * 10 + \
           [{"strategy": "overtake", "construction": "other", "sessions": 1000.0}] * 10
    by_c = estimate(rows, "construction")
    assert by_c["passes_comparison"].estimate > by_c["other"].estimate
    assert len(estimate(rows, "strategy")) == 2  # global + overtake: strategy not penalised per construction


# ── ranker ──────────────────────────────────────────────────────────────

def ev(strategy="overtake", strength=0.6, status="confirmed", ident="verified", n=0):
    return Event(strategy=strategy, film={"tmdb_id": n, "title": f"F{n}", "identity_class": ident}, period="2026-10-21",
                 axis="domestic", target={"type": "threshold", "value": 100 + n}, status=status, evidence={"x": n},
                 sources=[Source("tn_daily", "u", "2026-10-21")], reason="r", reason_codes=["c"], strength=strength)


def test_every_score_keeps_its_components_and_config_version():
    d = R.score_event(ev(), CFG, "run")
    assert set(d.score_components) == {"event_strength", "audience_affinity", "strategy_perf_adj", "headline_evidence",
                                       "novelty", "confidence", "penalties"}
    w = CFG["weights"]
    expected = sum(w[k] * d.score_components[k] for k in w)
    assert d.score == round(expected, 4) and d.priority_config_version == CFG["version"]
    assert d.performance["status"] == "calibration_pending" and d.score_components["strategy_perf_adj"] == 0.5


def test_penalties_and_confidence():
    legacy = R.score_event(ev(ident="legacy"), CFG, "r")
    prov = R.score_event(ev(status="provisional"), CFG, "r")
    clean = R.score_event(ev(), CFG, "r")
    assert legacy.score < clean.score and "legacy_identity" in legacy.score_components["penalties"]
    assert prov.score < clean.score and prov.score_components["confidence"] == 0.4


def _estimates(over, glob, n=20):
    from faramir.learning.estimates import Estimate

    return {"__global__": Estimate("global", 100, glob, (glob, glob), None, False),
            "overtake": Estimate("strategy=overtake", n, over, (over, over), None, n < 8)}


def test_performance_multiplier_is_capped_and_protected_for_small_delivery_counts():
    up, info = R.perf_component("overtake", _estimates(30000, 10000), {"overtake": 50}, CFG)
    assert info["multiplier"] == 1.3 and up == 1.0
    down, info = R.perf_component("overtake", _estimates(1000, 10000), {"overtake": 50}, CFG)
    assert info["multiplier"] == 0.7 and down == 0.0
    protected, info = R.perf_component("overtake", _estimates(1000, 10000), {"overtake": 3}, CFG)
    assert info["multiplier"] == 1.0                                       # < 8 deliveries: never adjusted down
    neutral, info = R.perf_component("overtake", _estimates(1000, 10000, n=3), {"overtake": 50}, CFG)
    assert neutral == 0.5 and info["status"] == "experimental"


def test_rank_slots_exploration_and_always_eligible():
    ds = [R.score_event(ev("overtake", 0.8, n=i), CFG, "r") for i in range(6)]
    ds.append(R.score_event(ev("countdown", 0.5, n=99), CFG, "r"))         # weakest, but least-delivered strategy
    ds.append(R.score_event(ev("chase", 0.95, n=50), CFG, "r"))
    out = R.rank(ds, slots=4, cfg=CFG, explored_this_week=0, deliveries_by_strategy={"overtake": 30, "chase": 30})
    suggested = [d for d in out if d.disposition["value"] == "suggested"]
    assert len(suggested) == 4
    (explore,) = [d for d in suggested if d.exploration["flag"]]
    assert explore.strategy_primary == "countdown" and "0 deliveries" in explore.exploration["reason"]
    assert out == R.rank(ds, slots=4, cfg=CFG, explored_this_week=0, deliveries_by_strategy={"overtake": 30, "chase": 30})
    assert [d.rank for d in sorted(ds, key=lambda d: d.rank)] == list(range(1, 9))


def test_exploration_prefers_a_strategy_not_already_in_the_main_slots():
    ds = [R.score_event(ev("overtake", 0.8, n=i), CFG, "r") for i in range(5)] + [R.score_event(ev("countdown", 0.5, n=9), CFG, "r")]
    out = R.rank(ds, slots=3, cfg=CFG, explored_this_week=0, deliveries_by_strategy={})
    (explore,) = [d for d in out if d.exploration["flag"]]
    assert explore.strategy_primary == "countdown"


def test_strong_events_are_deferred_not_dropped_when_out_of_slots():
    ds = [R.score_event(ev("overtake", 0.95, n=i), CFG, "r") for i in range(4)]
    out = R.rank(ds, slots=2, cfg={**CFG, "min_cards": 0}, explored_this_week=5)
    assert [d.disposition["value"] for d in out].count("deferred") == 2


# ── priority config proposals ───────────────────────────────────────────

def test_priority_proposal_is_a_versioned_diff_never_applied():
    p = priority.propose(CFG, {"weights.novelty": 0.12, "exploration.share": 0.2}, ["more exploration while n is small"],
                         "2026-11-01.v2")
    assert p["applied"] is False and p["to_version"] == "2026-11-01.v2" and p["from_version"] == CFG["version"]
    assert {d["key"] for d in p["diff"]} >= {"weights.novelty", "exploration.share", "version", "status"}
    assert priority.load()["weights"]["novelty"] == 0.10                   # the committed config is untouched


# ── explainer and performance report ────────────────────────────────────

def _seed(tmp_path):
    led = Ledger(str(tmp_path / "l"))
    led.append("events", [{"id": f"e{i}", "strategy": "overtake", "disposition": "primary"} for i in range(5)]
               + [{"id": "e9", "strategy": "overtake", "disposition": "suppressed"}]
               + [{"id": f"c{i}", "strategy": "chase", "disposition": "primary"} for i in range(5)])
    led.append("decisions", [{"id": f"r:e{i}", "strategy_primary": "overtake", "priority_config_version": "v1",
                               "disposition": {"value": "suggested" if i < 3 else "filtered", "reason_codes": ["below_slot_cutoff"]},
                               "exploration": {"flag": False}, "trigger": {"period": "2026-10-21"}} for i in range(5)]
               + [{"id": f"r:c{i}", "strategy_primary": "chase", "priority_config_version": "v1",
                   "disposition": {"value": "suggested" if i < 1 else "filtered", "reason_codes": ["below_slot_cutoff"]},
                   "exploration": {"flag": i == 0}, "trigger": {"period": "2026-10-21"}} for i in range(5)])
    led.append("deliveries", [{"id": "e0", "strategy": "overtake", "suggestion_id": "s0", "period": "2026-10-21"},
                              {"id": "e1", "strategy": "overtake", "suggestion_id": "s1", "period": "2026-10-21"},
                              {"id": "c0", "strategy": "chase", "suggestion_id": "s2", "period": "2026-10-21"}])
    led.append("editorial", [{"id": "x", "suggestion_id": "s0", "status": "published", "observed": "2026-10-22",
                              "published_url": "https://movieweb.com/a/", "headline": "A Passes B", "reason_codes": []},
                             {"id": "y", "suggestion_id": "s2", "status": "rejected", "observed": "2026-10-22",
                              "reason_codes": ["timing"]}])
    return led


def test_explainer_reproduces_counts_exactly(tmp_path):
    led = _seed(tmp_path)
    r = explainer.funnel(led)
    assert r == explainer.funnel(Ledger(str(tmp_path / "l")))
    assert r["funnel"]["overtake"] == {"detected": 6, "qualified": 6, "deduped": 5, "ranked": 5, "suggested": 3,
                                       "delivered": 2, "accepted": 1, "published": 1, "outcome": 0}
    assert r["drops"]["chase"]["accepted"] == {"timing": 1} and r["exploration_suggested"] == {"chase": 1}


def test_supply_differences_are_never_called_performance(tmp_path):
    r = explainer.funnel(_seed(tmp_path))
    assert "supply" in explainer.why_more(r, "overtake", "chase") and "performance" in explainer.why_more(r, "overtake", "chase")
    r["funnel"]["chase"]["detected"] = 6
    r["funnel"]["chase"]["qualified"] = 6
    assert "diverge at 'suggested'" in explainer.why_more(r, "overtake", "chase")


def test_performance_report_marks_calibration_pending_then_estimates(tmp_path):
    led = _seed(tmp_path)
    rep = performance.build(led, CFG)
    assert rep["calibration"].startswith("pending") and rep["acceptance"]["overtake"]["delivered"] == 2
    led.append("outcome_observations", [_obs("https://movieweb.com/a/", 14, 7000)])
    rep = performance.build(led, CFG)
    assert rep["calibration"] == "estimated" and rep["outcomes_14d_by_strategy"]["overtake"]["n"] == 1
    assert rep["outcomes_14d_by_strategy"]["overtake"]["is_experimental"]


# ── Aragorn refiner interface ───────────────────────────────────────────

from faramir.adapters.aragorn import NullRefiner, refine_and_validate  # noqa: E402
from tests.test_remediation import pick as vpick  # noqa: E402


class FakeRefiner:
    version = "fake-1"

    def __init__(self, out=None, boom=False):
        self.out, self.boom = out, boom

    def refine(self, headline, alt, evidence):
        if self.boom:
            raise RuntimeError("down")
        return {"headline": self.out, "alt": alt, "changes": ["reworded"], "refiner_version": self.version}


def test_null_refiner_changes_nothing():
    p = vpick("Alpha Film Passes Beta Story")
    out, rec = refine_and_validate(p, NullRefiner(), [])
    assert out is p and rec == {"used": False, "before": "Alpha Film Passes Beta Story", "after": "Alpha Film Passes Beta Story",
                                "refiner_version": "null-1", "changes": [], "reason": "unchanged"}


@pytest.mark.parametrize("refined, used", [
    ("Alpha Film Officially Passes Beta Story", True),
    ("Alpha Film Passes Beta Story With $900M Domestic", False),     # invented figure
    ("Beta Story Passes Alpha Film", False),                         # meaning reversed
    ("Alpha Film Passes Beta Story " + "x" * 80, False),             # over 85 characters
])
def test_refined_headlines_are_revalidated(refined, used):
    out, rec = refine_and_validate(vpick("Alpha Film Passes Beta Story"), FakeRefiner(refined), [])
    assert rec["used"] is used and (out["headline"] == refined) is used


def test_a_failing_refiner_never_blocks_the_pick():
    p = vpick("Alpha Film Passes Beta Story")
    out, rec = refine_and_validate(p, FakeRefiner(boom=True), [])
    assert out is p and rec["reason"].startswith("refiner error")


# ── generic event delivery: a weekend Takeover end to end ───────────────

from faramir import history as H  # noqa: E402
from faramir.tn_chart import parse_chart, weekend_url  # noqa: E402
from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.fixtures.tn_synth import page  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, pick_msgs  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def _weekend(gc, fri, films, identities):
    chart = parse_chart(page("weekend", fri, films=films), weekend_url(fri), "weekend", fri)
    at = datetime.combine(fri + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)
    assert H.collect(gc, "S", chart, run_id=f"r{fri}", retrieved_at=at, now=at, identities=identities).status == "committed"


def test_weekend_takeover_is_ranked_picked_validated_and_delivered(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setenv("FARAMIR_COLLECT_HISTORY", "true")
    book = env["gc"].book("S")
    book.add_tab(H.HISTORY_TAB, [H.HISTORY_HEADERS])
    book.add_tab(H.COLLECTIONS_TAB, [H.COLLECTION_HEADERS])
    ids = {"Beta-Story-(2026)": {"tmdb_id": 5001, "imdb_id": "tt5001001", "identity_confidence": "legacy",
                                 "identity_method": "state_legacy_title_year"}}
    row = lambda r, p, t, s, g, th, d: (r, p, t, s, g, "", th, g * 3, d)  # noqa: E731
    _weekend(env["gc"], date(2026, 10, 9), [row("1", "(new)", "Gamma Night", "Gamma-Night-(2026)", 30_000_000, 3900, 3),
                                            row("2", "(1)", "Beta Story", "Beta-Story-(2026)", 20_000_000, 3800, 24)], ids)
    _weekend(env["gc"], date(2026, 10, 16), [row("1", "(2)", "Beta Story", "Beta-Story-(2026)", 18_000_000, 3700, 31),
                                             row("2", "(1)", "Gamma Night", "Gamma-Night-(2026)", 12_000_000, 3900, 10)], ids)

    def respond(kwargs):
        payload = json.loads(kwargs["messages"][0]["content"])
        (c,) = [c for f in payload["active_films"] for c in f["candidates"] if c["kind"] == "event"]
        assert c["strategy"] == "takeover" and c["chart_rank"] == 1 and c["rank"]
        return json.dumps({"picks": [{
            "active_tmdb_id": 5001, "active_title": "Beta Story", "comp_tmdb_id": None, "comp_title": None,
            "axis": "weekend", "kind": "event", "threshold_value": None, "angle_category": "takeover",
            "headline": "Beta Story Takes No. 1 at the Weekend Box Office", "angle": "A new leader with $18.0M.",
            "claims": [{"type": "event", "comp_tmdb_id": None, "threshold": None, "axis": "weekend", "relation": None,
                        "genre": None, "text": "takes over the weekend chart"},
                       {"type": "chart_position", "comp_tmdb_id": None, "threshold": None, "axis": "weekend",
                        "relation": None, "genre": None, "text": "No. 1"}]}]})

    FakeAnthropic([respond]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    assert report["weekend_strategies"]["events"] == ["takeover:Beta Story:confirmed"]
    assert report["validation"][0]["accepted"], report["validation"]
    (msg,) = pick_msgs(env["slack"])
    text = json.dumps(msg["blocks"])
    assert "Takeover event" in text and "No. 1 on the weekend chart" in text and "Why: Takeover" in text
    (run_dir,) = (env["tmp"] / "out").iterdir()
    led = Ledger(str(run_dir / "ledger"))
    assert [d["strategy"] for d in led.read("deliveries")] == ["takeover"]
    assert any(d["strategy_primary"] == "takeover" and d["disposition"]["value"] == "suggested" for d in led.read("decisions"))


def test_event_pick_with_an_invented_figure_is_dropped():
    from faramir.validate import validate_pick

    ev_ = {"event": {"axis": "weekend", "kind": "event", "milestone_type": None, "threshold": None, "status": "confirmed"},
           "active": {"title": "Beta Story", "genres": []}, "comp": None, "categories": ["takeover"],
           "strategy_event": {"event_id": "e", "strategy": "takeover", "chart_rank": 1,
                              "values": {"weekend_gross": 18_000_000, "rank": 1}, "status": "confirmed"}}
    pick = {"active_title": "Beta Story", "headline": "Beta Story Takes No. 1 With $25M", "angle": "",
            "claims": [{"type": "event", "text": "takes over"}], "evidence": ev_}
    assert any(r.startswith("unverified_figure") for r in validate_pick(pick, []).reasons)
    pick["headline"] = "Beta Story Takes No. 1 With $18M"
    assert validate_pick(pick, []).ok


def test_a_single_outcome_has_no_interval_rather_than_a_zero_width_one():
    est = estimate([{"strategy": "overtake", "sessions": 7000}], "strategy")
    assert est["__global__"].interval is None and est["overtake"].interval is None
    assert est["overtake"].is_experimental


def test_weekend_story_reaches_the_writer_for_a_film_only_on_the_weekend_chart():
    from faramir import prioritize as prio
    from faramir.classify import classify
    from faramir.strategies.weekend import WeekendRow

    row = WeekendRow("Wicker-(2026)", "Wicker", 5, 5, 5_700_000, 1471, 9_800_000, 10, "ranked", "original",
                     1195803, "tt29611136", "high")
    e = Event(strategy="dark_horse", film={"tmdb_id": 1195803, "title": "Wicker", "identity_class": "verified"},
              period="2026-10-23", axis="weekend", target={"type": "rival", "value": "x"}, status="confirmed",
              evidence={"rank": 5, "weekend_gross": 5_700_000}, sources=[Source("tn_weekend_history", "r", "2026-10-23")],
              reason="Wicker out-grossed a wider release.", reason_codes=["theaters"], strength=0.7)
    args = ([], [], classify([e]), [], {}, {}, {"__global__": estimate([], "strategy")["__global__"]}, {}, {}, CFG, "r", 6, 0)
    payload, decisions = prio.prioritize(*args)
    assert payload == [] and decisions[0].disposition["reason_codes"] == ["film_not_active_in_payload"]
    payload, decisions = prio.prioritize(*args, weekend_rows=[row])
    (film,) = payload
    assert film["title"] == "Wicker" and film["source"] == "weekend_chart" and film["candidates"][0]["strategy"] == "dark_horse"


def test_discovery_card_keeps_this_weekends_gross_separate_from_last_weekends():
    from faramir.discovery.items import Claim, DiscoveryItem
    from faramir.prioritize import DiscoveryEvent

    item = DiscoveryItem("weekend_change_zscore", "2026-10-23", "YCSE-(2026)", "You Can See Everything",
                         "weekend_change_pct", 25.0, {}, [
                             Claim("YCSE-(2026)", "weekend_gross", 19_500_000, "2026-10-23", "c2:YCSE"),
                             Claim("YCSE-(2026)", "weekend_gross", 15_600_000, "2026-10-16", "c1:YCSE")],
                         "", "", "", [])
    ev = DiscoveryEvent(item, {"tmdb_id": 1})
    assert ev.evidence["weekend_gross"] == 19_500_000 and ev.evidence["previous_weekend_gross"] == 15_600_000


def test_one_film_cannot_take_every_slot():
    def dec(i, film, strategy, score):
        d = R.Decision.__new__(R.Decision)
        d.event_id, d.film, d.strategy_primary, d.score = f"e{i}", {"tmdb_id": film}, strategy, score
        d.score_components, d.exploration, d.rank = {"event_strength": 0.5}, {"flag": False}, None
        return d
    ds = [dec(0, 1, "overtake", .9), dec(1, 1, "overtake", .89), dec(2, 1, "chase", .88), dec(3, 1, "countdown", .87),
          dec(4, 2, "takeover", .5), dec(5, 3, "exit", .4)]
    out = R.rank(ds, 4, {**CFG, "exploration": {"share": 0, "min_slots_per_week": 0}}, explored_this_week=1)
    chosen = [(d.film["tmdb_id"], d.strategy_primary) for d in out if d.disposition["value"] == "suggested"]
    assert chosen == [(1, "overtake"), (1, "chase"), (2, "takeover"), (3, "exit")]
    assert next(d for d in out if d.event_id == "e1").disposition["reason_codes"] == ["same_film_story_already_in_slots"]


def test_obscure_comparisons_and_small_exits_score_lower():
    from faramir.strategies.common import comp_recognized, scale_strength
    assert not comp_recognized({"title": "The Interpreter"}, ["same_origin"], 900)
    assert comp_recognized({"title": "X"}, [], 120) and comp_recognized({"is_classic": True}, [], 900)
    assert comp_recognized({}, ["creative_head_to_head_director"], 900)
    assert scale_strength(9e6, 0.55) == 0.3 and scale_strength(46e6, 0.55) == 0.45 and scale_strength(300e6, 0.55) == 0.55


def test_too_weak_stories_get_no_slot():
    def dec(i, film, strategy, score, strength):
        d = R.Decision.__new__(R.Decision)
        d.event_id, d.film, d.strategy_primary, d.score = f"e{i}", {"tmdb_id": film}, strategy, score
        d.score_components, d.exploration, d.rank = {"event_strength": strength}, {"flag": False}, None
        return d
    ds = [dec(0, 1, "overtake", .67, .35), dec(1, 2, "dark_horse", .66, .7)]
    cfg = {**CFG, "min_event_strength": 0.4, "min_cards": 0, "exploration": {"share": 0, "min_slots_per_week": 0}}
    out = R.rank(ds, 4, cfg, 1)
    assert [d.event_id for d in out if d.disposition["value"] == "suggested"] == ["e1"]
    assert next(d for d in out if d.event_id == "e0").disposition["reason_codes"] == ["too_weak_a_story"]

    # the daily floor (Rob, 2026-11-03): with min_cards, the weak story is promoted, flagged, and ranked after the real one
    out = R.rank([dec(0, 1, "overtake", .67, .35), dec(1, 2, "dark_horse", .66, .7)], 4, {**cfg, "min_cards": 2}, 1)
    assert [d.event_id for d in out if d.disposition["value"] == "suggested"] == ["e0", "e1"]
    assert next(d for d in out if d.event_id == "e0").disposition["reason_codes"][-1] == "floor_fill"
    assert "floor_fill" not in next(d for d in out if d.event_id == "e1").disposition["reason_codes"]


def test_the_floor_never_invents_stories():
    def dec(i, film, strength):
        d = R.Decision.__new__(R.Decision)
        d.event_id, d.film, d.strategy_primary, d.score = f"e{i}", {"tmdb_id": film}, "chase", .5
        d.score_components, d.exploration, d.rank = {"event_strength": strength}, {"flag": False}, None
        return d
    cfg = {**CFG, "min_event_strength": 0.4, "min_cards": 2, "exploration": {"share": 0, "min_slots_per_week": 0}}
    out = R.rank([dec(0, 1, .3)], 4, cfg, 1)
    assert [d.disposition["value"] for d in out] == ["suggested"]          # one candidate: one card, never padded
    assert R.rank([], 4, cfg, 1) == []
