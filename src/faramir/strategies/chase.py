"""Chase (strategy 4): closing in on a *meaningful* comparison film.

Fires when F is within 10% [CAL] below a meaningful comparison on the same axis and the
pace projects passing it within 21 days [CAL]. Meaningful = franchise sibling, same director
or top-3 billed actor, a top-5 film in a shared genre, a notable film (Best Picture, classic,
$500M+ worldwide) or an all-time top-100 film on that axis.

Negative cases: ``already_passed`` (that is an Overtake), ``not_meaningful``,
``pace_insufficient`` (logged with the projected days), ``no_pace``, ``stale_figure``,
``suppressed`` (recently suggested), ``comp_metric_unavailable``.
"""

from __future__ import annotations

from datetime import date, timedelta

from faramir import inflation
from faramir.corpus import films_in_range, reported
from faramir.events.schema import Event
from faramir.matcher import detect_angle_categories
from faramir.strategies.common import (DetectorResult, all_time_rank, axis_source, comp_recognized, comp_ref, comp_strength,
                                       corpus_source, film_ref, meaningful, money, pace, series)

WITHIN_PCT = 0.10
MAX_DAYS_TO_PASS = 21
MAX_STALE_DAYS = 2
MAX_PER_FILM_AXIS = 3
# The daily floor (at least two cards a day): a chase that isn't a strong story is still emitted, weak (below the
# ranker's minimum strength), so it reaches a card only when nothing stronger fills the day.
FALLBACK_STRENGTH = 0.3
FALLBACK_MAX_DAYS = 60


def detect(film: dict, corpus: list[dict], corpus_index: dict, data_date: str, gross_as_of: str | None,
           suppressed_pairs: set | None = None, observations: list[dict] | None = None) -> DetectorResult:
    out = DetectorResult()
    suppressed_pairs = suppressed_pairs or set()
    for axis in ("domestic", "worldwide"):
        prev, cur, stale = series(film, axis)
        if cur <= 0:
            continue
        nearby = films_in_range(corpus_index, axis, int(cur), int(cur * (1 + WITHIN_PCT)))
        scored = []
        for comp in nearby:
            if comp.get("tmdb_id") == film.get("tmdb_id"):
                continue
            target = comp_ref(comp)
            value = reported(comp, axis)
            if value is None:
                out.reject("chase", film, axis, target, "comp_metric_unavailable", f"comparison {axis} gross not reported")
                continue
            if value <= cur:
                out.reject("chase", film, axis, target, "already_passed", "already passed: an Overtake, not a Chase")
                continue
            if (film.get("tmdb_id"), comp.get("tmdb_id"), axis) in suppressed_pairs:
                out.reject("chase", film, axis, target, "suppressed", "suggested within the suppression window")
                continue
            cats = detect_angle_categories(film, comp, corpus)
            rank = all_time_rank(value, corpus, axis, exclude_tmdb=comp.get("tmdb_id"))
            why = meaningful(cats, rank, comp)
            fallback = not why
            if stale >= MAX_STALE_DAYS:
                out.reject("chase", film, axis, target, "stale_figure", f"{axis} figure unchanged for {stale} days")
                continue
            per_day, basis = pace(film, axis, observations)
            gap = value - cur
            if per_day is None:
                out.reject("chase", film, axis, target, "no_pace", "no measurable pace")
                continue
            days = gap / per_day
            if days > MAX_DAYS_TO_PASS:
                if days > FALLBACK_MAX_DAYS:
                    out.reject("chase", film, axis, target, "pace_insufficient",
                               f"{money(gap)} gap at {money(per_day)}/day ≈ {days:.0f} days > {FALLBACK_MAX_DAYS}")
                    continue
                fallback = True
            if fallback and not comp_recognized(comp, cats, rank):
                out.reject("chase", film, axis, target, "not_meaningful",
                           "no franchise, talent, genre or notable link, and readers won't know the film")
                continue
            scored.append((comp, value, cats, rank, why or ["a comparison within 10%"], per_day, basis, gap, days, fallback))
        scored.sort(key=lambda t: (t[9], -comp_strength(t[2], t[3]), t[1]))
        strong = [t for t in scored if not t[9]][:MAX_PER_FILM_AXIS]
        weak = [t for t in scored if t[9]][:1]
        for comp, value, cats, rank, why, per_day, basis, gap, days, fallback in strong + weak:
            projected = (date.fromisoformat(data_date) + timedelta(days=max(1, round(days)))).isoformat()
            out.emit(Event(
                strategy="chase", film=film_ref(film), period=data_date, axis=axis, target=comp_ref(comp),
                status="anticipated",
                evidence={"current": cur, "target_value": value, "gap": gap, "gap_pct": round(gap / value, 4),
                          "pace_per_day": round(per_day), "pace_basis": basis, "days_to_pass": round(days, 1),
                          "projected_pass_date": projected, "categories": cats, "comp_all_time_rank": rank,
                          "meaningful_because": why},
                sources=[axis_source(film, axis, data_date), corpus_source(gross_as_of)],
                reason=(f"{film_ref(film)['title']} is {money(gap)} ({gap / value:.1%}) below {comp.get('title')} "
                        f"({comp.get('release_year')}) on the {axis} axis; at {money(per_day)}/day ({basis.replace('_', ' ')}) "
                        f"it passes in about {days:.0f} days. Meaningful because: {'; '.join(why)}."
                        + (f" {inflation.method_note(comp, axis)}" if comp.get("adjusted") else "")),
                reason_codes=["within_10pct_meaningful_comp" if not fallback else "fallback_weak_comparison",
                              f"pace:{basis}"] + [f"category:{x}" for x in cats] + (["classic_era"] if comp.get("era") else []),
                strength=FALLBACK_STRENGTH if fallback else comp_strength(cats, rank, base=0.45),
                candidate={"kind": "about_to_pass", "axis": axis, "threshold_value": value, "angle_categories": cats,
                           "comp": comp, "suppressed": False, "active_tmdb_id": film.get("tmdb_id"),
                           "comp_tmdb_id": comp.get("tmdb_id"), "comp_title": comp.get("title"),
                           "era": comp.get("era"), "fallback": fallback,
                           "richness_score": round(10 * (FALLBACK_STRENGTH if fallback else comp_strength(cats, rank, base=0.45)))}))
    return out
