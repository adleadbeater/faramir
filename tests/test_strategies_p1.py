"""Phase 1: event schema, ledger, Overtake/Chase/Countdown detectors, classifier and
overlap rules, dedup, rationale, editorial capture, registry and pipeline integration."""

import json
import os

import pytest

from faramir.classify import classify
from faramir.corpus import build_index
from faramir.dedup import dedup
from faramir.events.ledger import Ledger, default_ledger
from faramir.events.schema import Event, InvalidEvent, Source, checked
from faramir.strategies import chase, countdown, overtake, registry
from faramir.strategies.common import comp_ref, film_ref
from tests.test_matcher import make_corpus_film, make_film

DATE = "2026-10-21"


def film(**kw):
    base = make_film(tmdb_id=1, imdb_id="tt0000001", title="New Hit", canonical_title="New Hit",
                     domestic_yesterday=140_000_000, domestic_today=150_000_000,
                     worldwide_yesterday=300_000_000, worldwide_today=320_000_000,
                     dom_stale_days=0, ww_stale_days=0, identity_confidence="high")
    base.update(kw)
    return base


def comp(**kw):
    base = make_corpus_film(tmdb_id=500, imdb_id="tt0000500", title="Old Classic", release_year="2010",
                            domestic_lifetime=145_000_000, worldwide_lifetime=400_000_000,
                            domestic_status="reported", worldwide_status="reported", identity_status="verified")
    base.update(kw)
    return base


def comp_cand(c, axis="domestic", kind="just_passed", cats=()):
    return {"kind": kind, "axis": axis, "threshold_value": c[f"{axis}_lifetime"], "comp": c,
            "angle_categories": list(cats), "active_tmdb_id": 1, "comp_tmdb_id": c["tmdb_id"], "suppressed": False}


def ms_cand(value, axis="domestic", mtype="just_passed"):
    return {"kind": "milestone", "axis": axis, "threshold_value": value, "milestone_type": mtype, "comp": None,
            "angle_categories": ["round_number_milestone"], "suppressed": False}


# ── 1.1 schema and ledger ───────────────────────────────────────────────

def _event(**kw):
    base = dict(strategy="overtake", film=film_ref(film()), period=DATE, axis="domestic",
                target={"type": "threshold", "value": 150_000_000}, status="confirmed",
                evidence={"previous": 1, "current": 2}, sources=[Source("tn_daily", "url", DATE)],
                reason="r", reason_codes=["c"], strength=0.5)
    base.update(kw)
    return Event(**base)


def test_event_ids_are_stable_and_fingerprints_track_evidence():
    a, b = _event(), _event()
    assert a.event_id == b.event_id and a.fingerprint == b.fingerprint
    c = _event(evidence={"previous": 1, "current": 3})
    assert c.event_id == a.event_id and c.fingerprint != a.fingerprint
    d = _event(period="2026-10-22")
    assert d.event_id != a.event_id and d.story_key == a.story_key  # same story, another day
    assert _event(axis="worldwide").event_id != a.event_id


@pytest.mark.parametrize("bad", [
    dict(sources=[]), dict(sources=[Source("tn_daily", "", DATE)]), dict(evidence={}), dict(reason=""),
    dict(reason_codes=[]), dict(strategy="made_up"), dict(status="maybe"), dict(film={"title": "x"}),
    dict(strength=1.5),
])
def test_every_event_needs_source_evidence_identity_and_reason(bad):
    with pytest.raises(InvalidEvent):
        checked(_event(**bad))


def test_ledger_is_append_only_and_idempotent(tmp_path):
    led = Ledger(str(tmp_path))
    assert led.append("events", [{"id": "a", "v": 1}, {"id": "b"}], month="2026-10") == 2
    assert led.append("events", [{"id": "a", "v": 99}, {"id": "c"}], month="2026-11") == 1
    rows = led.read("events")
    assert [r["id"] for r in rows] == ["a", "b", "c"] and rows[0]["v"] == 1
    assert sorted(os.listdir(tmp_path / "events")) == ["2026-10.jsonl", "2026-11.jsonl"]
    with pytest.raises(ValueError):
        led.append("events", [{"v": 1}])


def test_dry_run_ledger_reads_persistent_but_writes_locally(tmp_path, monkeypatch):
    persistent = tmp_path / "persist"
    Ledger(str(persistent)).append("deliveries", [{"id": "old"}])
    monkeypatch.setenv("FARAMIR_LEDGER_DIR", str(persistent))
    dry = default_ledger(str(tmp_path / "run"), dry_run=True)
    assert [r["id"] for r in dry.read("deliveries")] == ["old"]
    dry.append("deliveries", [{"id": "new"}])
    assert [r["id"] for r in Ledger(str(persistent)).read("deliveries")] == ["old"]  # persistent untouched
    live = default_ledger(str(tmp_path / "run2"), dry_run=False)
    assert live.root == str(persistent)


# ── 1.2 Overtake ────────────────────────────────────────────────────────

def test_overtake_comp_crossing_is_confirmed_with_evidence():
    c = comp()
    r = overtake.detect(film(), [comp_cand(c, cats=["franchise_sibling"])], [c], DATE, "2026-10-01")
    (e,) = r.events
    assert (e.strategy, e.status, e.subtype, e.axis) == ("overtake", "confirmed", "comp", "domestic")
    assert e.evidence["previous"] == 140_000_000 and e.evidence["target_value"] == 145_000_000
    assert [s.name for s in e.sources] == ["tn_daily", "corpus_v2"] and "category:franchise_sibling" in e.reason_codes
    assert "passed Old Classic (2010)" in e.reason


def test_overtake_milestone_crossing():
    (e,) = overtake.detect(film(), [ms_cand(150_000_000)], [], DATE, None).events
    assert e.subtype == "milestone" and e.target == {"type": "threshold", "value": 150_000_000}


@pytest.mark.parametrize("kw, code", [
    (dict(domestic_yesterday=0), "first_observation"),
    (dict(domestic_yesterday=146_000_000), "not_crossed"),
    (dict(dom_stale_days=2), "stale_figure"),
])
def test_overtake_negative_cases(kw, code):
    c = comp()
    r = overtake.detect(film(**kw), [comp_cand(c)], [c], DATE, None)
    assert r.events == [] and r.rejected[0]["reason_code"] == code


def test_overtake_old_crossing_and_provisional_source():
    c = comp()
    assert overtake.detect(film(), [comp_cand(c)], [c], DATE, None, days_since_previous=4).rejected[0]["reason_code"] == "crossing_too_old"
    (e,) = overtake.detect(film(), [comp_cand(c)], [c], DATE, None, provisional=True).events
    assert e.status == "provisional"


def test_overtake_records_all_time_rank_crossing():
    corpus = [comp(tmdb_id=600 + i, domestic_lifetime=145_000_000 + i) for i in range(3)]
    (e,) = overtake.detect(film(), [ms_cand(150_000_000)], corpus, DATE, None).events
    assert e.evidence["all_time_rank_before"] == 4 and e.evidence["all_time_rank_after"] == 1
    assert "all_time_rank_crossing" in e.reason_codes


def test_overtake_rejects_missing_comp_metric():
    c = comp(domestic_lifetime=None, domestic_status="missing")
    cand = comp_cand(comp(), cats=[])
    cand["comp"] = c
    assert overtake.detect(film(), [cand], [c], DATE, None).rejected[0]["reason_code"] == "comp_metric_unavailable"


# ── 1.2 Chase ───────────────────────────────────────────────────────────

def _chase(f, corpus, **kw):
    return chase.detect(f, corpus, build_index(corpus), DATE, "2026-10-01", **kw)


def test_chase_fires_for_a_meaningful_comp_within_pace():
    c = comp(domestic_lifetime=155_000_000, collection_id=7)
    (e,) = [x for x in _chase(film(collection_id=7), [c]).events if x.axis == "domestic"]
    assert e.status == "anticipated" and e.evidence["gap"] == 5_000_000 and e.evidence["pace_basis"] == "1_day_change"
    assert e.evidence["days_to_pass"] == 0.5 and "franchise sibling" in e.evidence["meaningful_because"]
    assert e.candidate["kind"] == "about_to_pass" and e.candidate["comp"] is c


BIGGER = [comp(tmdb_id=900 + i, imdb_id=f"tt9{i:06d}", domestic_lifetime=400_000_000 + i) for i in range(100)]


@pytest.mark.parametrize("kw, ckw, code", [
    ({}, dict(domestic_lifetime=155_000_000), "not_meaningful"),                               # unrelated and unknown
    (dict(collection_id=7, domestic_yesterday=149_990_000), dict(domestic_lifetime=164_000_000, collection_id=7), "pace_insufficient"),
    (dict(collection_id=7, domestic_yesterday=150_000_000, dom_stale_days=1), dict(domestic_lifetime=155_000_000, collection_id=7), "no_pace"),
    (dict(collection_id=7, dom_stale_days=2), dict(domestic_lifetime=155_000_000, collection_id=7), "stale_figure"),
])
def test_chase_negative_cases(kw, ckw, code):
    r = _chase(film(**kw), [comp(**ckw)] + BIGGER * 4)  # 400 bigger films: the comp is neither top 100 nor widely known
    assert not [e for e in r.events if e.axis == "domestic"]
    assert code in [x["reason_code"] for x in r.rejected if x["axis"] == "domestic"]


def test_chase_keeps_an_unrelated_but_known_comp_only_as_a_weak_fallback():
    # all-time rank 101-300: readers know it, nothing links the two films. Below the ranker's minimum strength, it
    # reaches a card only when nothing stronger fills the daily floor of two cards (2026-11-03).
    r = _chase(film(), [comp(domestic_lifetime=155_000_000)] + BIGGER)
    (e,) = [x for x in r.events if x.axis == "domestic"]
    assert e.strength == 0.3 and e.candidate["fallback"] is True and "fallback_weak_comparison" in e.reason_codes


def test_chase_never_fires_on_an_already_passed_comp():
    r = _chase(film(collection_id=7), [comp(domestic_lifetime=150_000_000, collection_id=7)])
    assert not [e for e in r.events if e.axis == "domestic"]


def test_chase_respects_suppression():
    r = _chase(film(collection_id=7), [comp(domestic_lifetime=155_000_000, collection_id=7)], suppressed_pairs={(1, 500, "domestic")})
    assert "suppressed" in [x["reason_code"] for x in r.rejected]


def test_chase_all_time_top_100_is_meaningful():
    target = comp(domestic_lifetime=155_000_000)                                   # rank 1 in this tiny corpus
    (e,) = [x for x in _chase(film(), [target]).events if x.axis == "domestic"]
    assert any("all-time top 100" in w for w in e.evidence["meaningful_because"])


def test_chase_uses_a_seven_day_observation_series():
    obs = [{"film_key": "tt0000001", "axis": "domestic", "date": "2026-10-14", "value": 115_000_000},
           {"film_key": "tt0000001", "axis": "domestic", "date": "2026-10-21", "value": 150_000_000}]
    c = comp(domestic_lifetime=160_000_000, collection_id=7)
    (e,) = [x for x in _chase(film(collection_id=7), [c], observations=obs).events if x.axis == "domestic"]
    assert e.evidence["pace_basis"] == "7_day_average" and e.evidence["pace_per_day"] == 5_000_000


# ── 1.2 Countdown ───────────────────────────────────────────────────────

def test_countdown_fires_inside_the_gap_and_pace_limits():
    (e,) = countdown.detect(film(domestic_today=196_000_000, domestic_yesterday=194_000_000), DATE, thresholds=[200_000_000]).events
    assert e.evidence["gap"] == 4_000_000 and e.evidence["gap_limit"] == 10_000_000 and e.evidence["days_to_cross"] == 2.0


@pytest.mark.parametrize("kw, code", [
    (dict(domestic_today=185_000_000, domestic_yesterday=184_000_000), "gap_too_large"),            # $15M gap
    (dict(domestic_today=196_000_000, domestic_yesterday=196_000_000, dom_stale_days=1), "stale_figure"),
    (dict(domestic_today=192_000_000, domestic_yesterday=191_500_000), "pace_insufficient"),        # 16 days
])
def test_countdown_negative_cases(kw, code):
    r = countdown.detect(film(**kw), DATE, thresholds=[200_000_000])
    assert not [e for e in r.events if e.axis == "domestic"]
    assert code in [x["reason_code"] for x in r.rejected]


def test_countdown_small_milestone_uses_the_five_percent_limit():
    f = film(domestic_today=94_000_000, domestic_yesterday=93_000_000)                  # $6M gap > 5% of $100M
    assert countdown.detect(f, DATE, thresholds=[100_000_000]).rejected[0]["reason_code"] == "gap_too_large"
    assert countdown.detect(film(domestic_today=96_000_000, domestic_yesterday=95_000_000), DATE, thresholds=[100_000_000]).events


def test_countdown_unchanged_gap_is_suppressed_until_halved():
    f = film(domestic_today=196_000_000, domestic_yesterday=194_000_000)
    r = countdown.detect(f, DATE, thresholds=[200_000_000], prior_gaps={("domestic", 200_000_000): 6_000_000})
    assert r.rejected[0]["reason_code"] == "gap_not_shrunk"
    r = countdown.detect(f, DATE, thresholds=[200_000_000], prior_gaps={("domestic", 200_000_000): 8_000_000})
    assert r.events and "gap_halved_since_last" in r.events[0].reason_codes


def test_countdown_never_fires_on_a_crossed_threshold():
    assert countdown.detect(film(domestic_today=201_000_000), DATE, thresholds=[200_000_000]).events == []


# ── 1.3 classifier and overlap rules ────────────────────────────────────

def test_one_primary_per_cluster_and_completed_beats_anticipated():
    c = comp()
    o = _event(target=comp_ref(c), evidence={"target_value": 145e6})
    ch = _event(strategy="chase", status="anticipated", target=comp_ref(c), evidence={"target_value": 145e6, "gap": 1})
    (cl,) = classify([ch, o])
    assert cl.primary.strategy == "overtake" and cl.secondary is None
    assert cl.rejected_alternatives == [{"event_id": ch.event_id, "strategy": "chase", "reason_code": "completed_beats_anticipated"}]


def test_chase_and_countdown_within_two_percent_share_a_cluster():
    ch = _event(strategy="chase", status="anticipated", target=comp_ref(comp(domestic_lifetime=201_000_000)),
                evidence={"target_value": 201_000_000}, strength=0.7)
    cd = _event(strategy="countdown", status="anticipated", target={"type": "threshold", "value": 200_000_000},
                evidence={"target_value": 200_000_000}, strength=0.6)
    (cl,) = classify([ch, cd])
    assert (cl.primary.strategy, cl.secondary.strategy) == ("chase", "countdown")
    far = _event(strategy="countdown", status="anticipated", target={"type": "threshold", "value": 250_000_000},
                 evidence={"target_value": 250_000_000})
    assert len(classify([ch, far])) == 2


def test_provisional_primary_is_deferred():
    (cl,) = classify([_event(status="provisional")])
    assert cl.disposition == "deferred" and "primary_provisional" in cl.reason_codes


def test_duplicate_strategy_in_a_cluster_is_rejected():
    a, b = _event(strength=0.9), _event(strength=0.4, evidence={"x": 1})
    (cl,) = classify([a, b])
    assert cl.rejected_alternatives[0]["reason_code"] == "duplicate_in_cluster"


# ── 1.4 dedup ───────────────────────────────────────────────────────────

def _delivery(e, period, gap=None):
    return {"story_key": e.story_key, "period": period, "evidence": {"gap": gap} if gap else {}}


def test_dedup_rules():
    o = classify([_event()])
    kept, sup = dedup(o, [_delivery(o[0].primary, "2026-09-01")], DATE)
    assert kept == [] and sup[0]["reason_code"] == "already_delivered"

    ch = _event(strategy="chase", status="anticipated", evidence={"gap": 4e6})
    cl = classify([ch])
    assert dedup(cl, [_delivery(ch, "2026-10-19", gap=5e6)], DATE)[1][0]["reason_code"] == "unchanged_anticipated"
    assert dedup(cl, [_delivery(ch, "2026-10-19", gap=9e6)], DATE)[0]           # gap more than halved: new news
    assert dedup(cl, [_delivery(ch, "2026-10-01", gap=5e6)], DATE)[0]           # outside 14 days
    assert dedup(cl, [], DATE)[0]


# ── 1.5 rationale and 1.6 editorial ─────────────────────────────────────

def test_rationale_is_built_from_evidence_and_names_sources():
    c = comp()
    (e,) = overtake.detect(film(), [comp_cand(c)], [c], DATE, "2026-10-01").events
    from faramir import rationale

    line = rationale.line(e)
    assert line.startswith("Overtake · domestic $150.0M passed Old Classic (2010) at $145.0M; $140.0M the day before")
    assert "tn_daily 2026-10-21" in line and "corpus_v2 2026-10-01" in line


def test_editorial_statuses_and_reason_codes():
    from datetime import date

    from faramir.editorial import capture, derive

    today = date(2026, 10, 25)
    base = {"run_date": "2026-10-21", "delivery_state": "posted", "suggestion_id": "s1"}
    assert derive({**base, "thumbs_up": "2"}, today)[0] == "accepted"
    assert derive({**base, "thumbs_down": "1", "thread_feedback": "we already covered this"}, today)[:2] == ("rejected", ["already_covered"])
    assert derive({**base, "thumbs_down": "1", "thread_feedback": "numbers look off and it's too late"}, today)[1] == ["timing", "questionable_data"]
    assert derive({**base, "thread_feedback": "Assigned to Dana"}, today)[0] == "assigned"
    assert derive({**base, "thread_feedback": "up: https://movieweb.com/new-hit-box-office/"}, today)[2] == "https://movieweb.com/new-hit-box-office/"
    assert derive({**base, "run_date": "2026-10-01"}, today)[0] == "expired"
    rows = capture([{**base, "thumbs_up": "1"}, {**base, "suggestion_id": "s2", "delivery_state": "planned"}], today)
    assert [r["status"] for r in rows] == ["accepted"]
    assert capture([{**base, "thumbs_up": "1"}], today)[0]["id"] == rows[0]["id"]  # stable while the status holds


def test_registry_accounts_for_all_thirteen_strategies_and_discovery():
    reg = registry()
    assert sorted(v["number"] for v in reg["strategies"].values()) == list(range(1, 14))
    assert "discovery" in reg and all(v["data_status"] in reg["statuses"] for v in reg["strategies"].values())
    live = {k for k, v in reg["strategies"].items() if v["data_status"] == "live"}
    assert live == {"overtake", "chase", "countdown", "star_record"}
    assert {k for k in live if reg["strategies"][k]["enabled"]} == {"overtake", "chase", "countdown"}
    assert not reg["strategies"]["premium_run"]["enabled"] and reg["strategies"]["premium_run"]["data_status"] == "blocked_no_source"


# ── 1.7 pipeline integration ────────────────────────────────────────────

from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, pick_first_candidate, pick_msgs, state_after  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def _ledger_rows(env, stream):
    (run_dir,) = (env["tmp"] / "out").iterdir()
    return Ledger(str(run_dir / "ledger")).read(stream)


def test_every_candidate_and_event_gets_a_ledger_row_and_picks_carry_rationale(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    eng = report["strategy_engine"]
    assert eng["delivered_clusters"] >= 1
    payload = json.loads(fake.calls[0]["messages"][0]["content"])
    sent = [c for f in payload["active_films"] for c in f["candidates"]]
    # Phase 4: only the ranked slots (picks_max + 2) reach Claude, in rank order.
    assert 1 <= len(sent) <= 6 and all(c.get("rank") for c in sent)
    # Variety (priority config v2): one slot per film × story type, at most two per film.
    assert len({c["strategy"] for c in sent}) == len(sent) <= 2
    assert all(c.get("strategy") in ("overtake", "chase", "countdown") for c in sent)
    cands = _ledger_rows(env, "candidates")
    assert len(cands) >= report["candidates"]  # every matcher candidate (plus milestones) has a row
    assert all(r["disposition"] in ("event", "rejected", "not_a_strategy_event") for r in cands)
    events = _ledger_rows(env, "events")
    assert events and all(r["disposition"] in ("primary", "secondary", "rejected_alternative", "suppressed", "deferred")
                          for r in events)
    (msg,) = pick_msgs(env["slack"])
    assert "Why: Overtake" in json.dumps(msg["blocks"])
    (delivered,) = _ledger_rows(env, "deliveries")
    assert delivered["strategy"] == "overtake"


def test_no_duplicate_cluster_is_ever_sent_to_claude(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    main(env)
    payload = json.loads(fake.calls[0]["messages"][0]["content"])
    events = {r["event_id"]: r for r in _ledger_rows(env, "events")}
    keys = [tuple(events[c["event_id"]]["cluster"]) for f in payload["active_films"] for c in f["candidates"]]
    assert len(keys) == len(set(keys))


def test_zero_pick_day_works(env, monkeypatch):
    from faramir import strategy_engine

    real = strategy_engine.run_daily

    def nothing(*a, **k):
        r = real(*a, **k)
        r.payload_films = [{**f, "candidates": []} for f in r.payload_films]
        return r

    monkeypatch.setattr(strategy_engine, "run_daily", nothing)
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 0
    assert fake.calls == [] and pick_msgs(env["slack"]) == []
    assert "0 editorial picks" in json.dumps(header_msgs(env["slack"])[0]["blocks"])


def test_persistent_ledger_dedups_across_runs(env, tmp_path):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setenv("FARAMIR_LEDGER_DIR", str(tmp_path / "ledger"))
    FakeAnthropic([pick_first_candidate, pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    first = Ledger(str(tmp_path / "ledger")).read("deliveries")
    for p in list((env["tmp"] / "out").iterdir()):
        __import__("shutil").rmtree(p)
    assert main(env) == 0
    report, _, _ = artifacts(env)
    stories = [d["story_key"] for d in Ledger(str(tmp_path / "ledger")).read("deliveries")]
    assert first and len(stories) == len(set(stories))  # a delivered story is never delivered again
    assert len({m["text"] for m in pick_msgs(env["slack"])}) == len(pick_msgs(env["slack"]))  # nothing reposted


def test_same_day_rerun_keeps_the_previous_day_figure(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate, pick_first_candidate]).install(env["monkeypatch"])
    main(env)
    beta = {r["tmdb_id"]: r for r in state_after(env["gc"])}[5001]
    assert (beta["domestic_yesterday"], beta["domestic_today"]) == (140_000_000, 152_000_000)
    for p in list((env["tmp"] / "out").iterdir()):
        __import__("shutil").rmtree(p)
    main(env)
    beta = {r["tmdb_id"]: r for r in state_after(env["gc"])}[5001]
    assert (beta["domestic_yesterday"], beta["domestic_today"], beta["dom_stale_days"]) == (140_000_000, 152_000_000, 0)
