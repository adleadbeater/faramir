"""Overtake (strategy 5): a confirmed crossing, never a projection.

Fires when ``prev_cum < target ≤ cur_cum`` on the same source series (TN cumulative for
domestic, BOM for worldwide), the previous figure was actually observed (not a first
sighting), the current figure is fresh and the crossing happened within 3 days [CAL].
Targets: a comparison film's lifetime gross (corpus) or a round-number milestone; the
all-time rank before and after the crossing is recorded as evidence (list-rank crossing).

Negative cases (reason codes): ``first_observation`` (no previous figure, so the crossing
can't be dated), ``stale_figure`` (unchanged ≥ 2 days), ``not_crossed``, ``provisional_source``,
``comp_metric_unavailable``.
"""

from __future__ import annotations

from faramir.corpus import reported
from faramir.events.schema import Event
from faramir.strategies.common import (DetectorResult, all_time_rank, axis_source, comp_ref, comp_strength,
                                       corpus_source, film_ref, money, series, threshold_strength)

MAX_STALE_DAYS = 2
CROSSING_WINDOW_DAYS = 3


def detect(film: dict, candidates: list[dict], corpus: list[dict], data_date: str, gross_as_of: str | None,
           provisional: bool = False, days_since_previous: int = 1) -> DetectorResult:
    out = DetectorResult()
    for c in candidates:
        is_comp = c.get("kind") == "just_passed"
        is_milestone = c.get("kind") == "milestone" and c.get("milestone_type") == "just_passed"
        if not (is_comp or is_milestone):
            continue
        axis = c["axis"]
        prev, cur, stale = series(film, axis)
        if is_comp:
            comp = c["comp"]
            target = comp_ref(comp)
            value = reported(comp, axis)
            if value is None:
                out.reject("overtake", film, axis, target, "comp_metric_unavailable", f"comparison {axis} gross not reported")
                continue
        else:
            value = float(c["threshold_value"])
            target = {"type": "threshold", "value": int(value)}
        if prev <= 0:
            out.reject("overtake", film, axis, target, "first_observation",
                       f"no previous {axis} figure; the crossing cannot be dated")
            continue
        if not (prev < value <= cur):
            out.reject("overtake", film, axis, target, "not_crossed", f"{money(prev)} → {money(cur)} vs {money(value)}")
            continue
        if stale >= MAX_STALE_DAYS:
            out.reject("overtake", film, axis, target, "stale_figure", f"{axis} figure unchanged for {stale} days")
            continue
        if days_since_previous > CROSSING_WINDOW_DAYS:
            out.reject("overtake", film, axis, target, "crossing_too_old", f"{days_since_previous} days since the last figure")
            continue
        status = "provisional" if provisional else "confirmed"
        rank_before = all_time_rank(prev, corpus, axis, exclude_tmdb=film.get("tmdb_id"))
        rank_after = all_time_rank(cur, corpus, axis, exclude_tmdb=film.get("tmdb_id"))
        sources = [axis_source(film, axis, data_date)]
        if is_comp:
            sources.append(corpus_source(gross_as_of))
            cats = list(c.get("angle_categories") or [])
            comp_rank = all_time_rank(value, corpus, axis, exclude_tmdb=comp.get("tmdb_id"))
            strength = comp_strength(cats, comp_rank, base=0.55)
            reason = (f"{film_ref(film)['title']} passed {comp.get('title')} ({comp.get('release_year')}) on the {axis} axis: "
                      f"{money(prev)} → {money(cur)} against {money(value)} (lifetime, corpus as of {gross_as_of}).")
            codes = ["crossed_comp_lifetime"] + [f"category:{x}" for x in cats]
        else:
            cats = []
            strength = threshold_strength(value)
            reason = f"{film_ref(film)['title']} crossed {money(value)} {axis}: {money(prev)} → {money(cur)}."
            codes = ["crossed_round_threshold"]
        if rank_after < rank_before:
            codes.append("all_time_rank_crossing")
        out.emit(Event(
            strategy="overtake", film=film_ref(film), period=data_date, axis=axis, target=target, status=status,
            evidence={"previous": prev, "current": cur, "target_value": value, "stale_days": stale,
                      "days_since_previous": days_since_previous, "all_time_rank_before": rank_before,
                      "all_time_rank_after": rank_after, "categories": cats},
            sources=sources, reason=reason, reason_codes=codes,
            subtype="comp" if is_comp else "milestone", strength=strength, candidate=c))
    return out
