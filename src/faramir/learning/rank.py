"""Transparent ranker and decision records (plan §6).

score = Σ weight·component − penalties, every component kept on the record:

* ``event_strength`` — the detector's strength;
* ``audience_affinity`` — Cerebro snapshot tier for the film/talent/franchise (0.5 if unknown);
* ``strategy_perf_adj`` — the strategy's performance multiplier vs the global estimate, capped
  ×0.7–×1.3; neutral while calibration is pending; never adjusted down before 8 deliveries;
* ``headline_evidence`` — the measured lift of the recommended construction (0.5 if none);
* ``novelty`` — 1 for a new story (Discovery: its novelty score);
* ``confidence`` — identity class × event status.

An event with strength ≥ 0.9 stays eligible regardless. Exploration reserves 15% of slots
(at least one a week) for strategies with the least evidence and marks those picks
``exploration=true`` with the reason. Ties and exploration choices are deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from faramir.analytics.audience import affinity_score

IDENTITY_CONF = {"verified": 1.0, "manual": 1.0, "legacy": 0.7, "unresolved": 0.0}
STATUS_CONF = {"confirmed": 1.0, "anticipated": 0.8, "provisional": 0.4, "unverifiable": 0.0}


@dataclass
class Decision:
    run_id: str
    event_id: str
    fingerprint: str
    film: dict
    strategy_primary: str
    strategy_secondary: str | None
    rejected_alternatives: list[dict]
    trigger: dict
    audience: dict
    performance: dict
    score_components: dict
    score: float
    priority_config_version: str
    rank: int = 0
    competing_above: list[str] = field(default_factory=list)
    exploration: dict = field(default_factory=lambda: {"flag": False, "reason": None})
    disposition: dict = field(default_factory=lambda: {"value": "ranked", "reason_codes": []})
    headline: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)

    def record(self) -> dict:
        d = dict(self.__dict__)
        d["id"] = f"{self.run_id}:{self.event_id}"
        return d


def perf_component(strategy: str, estimates: dict, deliveries_by_strategy: dict, cfg: dict) -> tuple[float, dict]:
    lo, hi = cfg["performance_multiplier_cap"]
    g = estimates.get("__global__")
    s = estimates.get(strategy)
    if g is None or g.status == "calibration_pending" or s is None or s.is_experimental or not g.estimate:
        return 0.5, {"scope": strategy, "status": "calibration_pending" if (g is None or g.status == "calibration_pending")
                     else "experimental", "n": getattr(s, "n", 0), "multiplier": 1.0}
    mult = max(lo, min(hi, s.estimate / g.estimate))
    if mult < 1 and deliveries_by_strategy.get(strategy, 0) < cfg["min_deliveries_before_down_adjust"]:
        mult = 1.0
    return round((mult - lo) / (hi - lo), 4), {"scope": strategy, "status": "estimated", "n": s.n, "estimate": s.estimate,
                                               "interval": s.interval, "multiplier": round(mult, 3)}


def headline_component(rec) -> float:
    if rec is None or not rec.constructions or not rec.constructions[0].get("evidence") \
            or rec.constructions[0]["rank_basis"] != "measured":
        return 0.5
    lift = rec.constructions[0]["evidence"]["lift_vs_baseline"] or 0.0
    return round(max(0.0, min(1.0, 0.5 + lift / 4)), 4)


def score_event(event, cfg: dict, run_id: str, audience_signal: dict | None = None, estimates: dict | None = None,
                deliveries_by_strategy: dict | None = None, headline_rec=None, novelty: float = 1.0,
                secondary=None, rejected_alternatives=None) -> Decision:
    w = cfg["weights"]
    perf, perf_info = perf_component(event.strategy, estimates or {}, deliveries_by_strategy or {}, cfg)
    ident = IDENTITY_CONF.get(event.film.get("identity_class") or "legacy", 0.7)
    comps = {"event_strength": event.strength, "audience_affinity": affinity_score(audience_signal),
             "strategy_perf_adj": perf, "headline_evidence": headline_component(headline_rec), "novelty": novelty,
             "confidence": round(ident * STATUS_CONF.get(event.status, 0.0), 4)}
    penalties = {}
    if event.status == "provisional":
        penalties["provisional"] = cfg["penalties"]["provisional"]
    if event.film.get("identity_class") == "legacy":
        penalties["legacy_identity"] = cfg["penalties"]["legacy_identity"]
    score = round(sum(w[k] * v for k, v in comps.items()) - sum(penalties.values()), 4)
    return Decision(run_id, event.event_id, event.fingerprint, event.film, event.strategy,
                    secondary.strategy if secondary else None, list(rejected_alternatives or []),
                    {"metric": event.axis, "values": event.evidence, "period": event.period,
                     "sources": [s.__dict__ for s in event.sources], "validation_status": event.status},
                    {"signal": audience_signal, "tier": (audience_signal or {}).get("tier", "unknown")}, perf_info,
                    {**comps, "penalties": penalties}, score, cfg["version"],
                    headline=headline_rec.guidance() if headline_rec else {})


def _exploration_slots(slots: int, cfg: dict, explored_this_week: int) -> int:
    want = max(round(cfg["exploration"]["share"] * slots), 0)
    if explored_this_week < cfg["exploration"]["min_slots_per_week"]:
        want = max(want, 1)
    return min(want, slots)


def rank(decisions: list[Decision], slots: int, cfg: dict, explored_this_week: int = 0,
         deliveries_by_strategy: dict | None = None) -> list[Decision]:
    """Order decisions, mark the top ``slots`` as suggested (with exploration), the rest filtered."""
    deliveries_by_strategy = deliveries_by_strategy or {}
    ordered = sorted(decisions, key=lambda d: (-d.score, d.event_id))
    for i, d in enumerate(ordered, 1):
        d.rank = i
        d.competing_above = [x.event_id for x in ordered[:i - 1]][:5]
    n_explore = _exploration_slots(slots, cfg, explored_this_week) if len(ordered) > slots else 0
    # Variety: one slot per film × story type, and at most max_per_film slots per film, so one film
    # passing several comparison films can't take the whole day.
    div = cfg.get("diversity", {})
    max_per_film = div.get("max_per_film", 2)
    main, crowded, per_film, seen = [], set(), {}, set()
    too_weak = {d.event_id for d in ordered if d.score_components["event_strength"] < cfg.get("min_event_strength", 0)}
    for d in ordered:
        if len(main) >= slots - n_explore:
            break
        if d.event_id in too_weak:
            continue
        film = (d.film or {}).get("tmdb_id") or (d.film or {}).get("title")
        if (film, d.strategy_primary) in seen or per_film.get(film, 0) >= max_per_film:
            crowded.add(d.event_id)
            continue
        main.append(d)
        seen.add((film, d.strategy_primary))
        per_film[film] = per_film.get(film, 0) + 1
    in_main_ids = {d.event_id for d in main}
    rest = [d for d in ordered if d.event_id not in in_main_ids and d.event_id not in crowded and d.event_id not in too_weak]
    # Exploration: the least-evidenced strategies first (fewest deliveries), deterministic tie-break.
    in_main = {d.strategy_primary for d in main}
    rest_sorted = sorted(rest, key=lambda d: (d.strategy_primary in in_main,  # test something not already shown
                                              deliveries_by_strategy.get(d.strategy_primary, 0),
                                              -d.score,
                                              hashlib.sha1(d.event_id.encode()).hexdigest()))
    explore = rest_sorted[:n_explore]
    chosen = {d.event_id for d in main + explore}
    # The daily floor: when fewer than ``min_cards`` (plus two spares, for titles the fact check passes on) were
    # chosen, promote the best of the rest, weak or crowded ones included. They are flagged floor_fill, so the
    # card stage titles them only while the day is still short of its floor.
    floor_fill: set[str] = set()
    want = cfg.get("min_cards", 0)
    if want and len(chosen) < want + 2:
        pool = sorted((d for d in ordered if d.event_id not in chosen), key=lambda d: (-d.score, d.event_id))
        for d in pool[:want + 2 - len(chosen)]:
            floor_fill.add(d.event_id)
        chosen |= floor_fill
    for d in ordered:
        if d.event_id in floor_fill:
            d.disposition = {"value": "suggested", "reason_codes": [f"rank_{d.rank}", "floor_fill"]}
        elif d.event_id in chosen:
            d.disposition = {"value": "suggested", "reason_codes": [f"rank_{d.rank}"]}
        elif d.event_id in too_weak:
            d.disposition = {"value": "filtered", "reason_codes": ["too_weak_a_story"]}
        elif d.event_id in crowded:
            d.disposition = {"value": "filtered", "reason_codes": ["same_film_story_already_in_slots"]}
        elif d.score_components["event_strength"] >= cfg["always_eligible_strength"]:
            d.disposition = {"value": "deferred", "reason_codes": ["no_slot_but_always_eligible"]}
        else:
            d.disposition = {"value": "filtered", "reason_codes": ["below_slot_cutoff"]}
    for d in explore:
        d.exploration = {"flag": True, "reason": f"exploration slot: {d.strategy_primary} has "
                                                 f"{deliveries_by_strategy.get(d.strategy_primary, 0)} deliveries"}
    return [d for d in ordered if d.event_id in chosen] + [d for d in ordered if d.event_id not in chosen]
