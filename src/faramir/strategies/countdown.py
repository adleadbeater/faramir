"""Countdown (strategy 8): closing in on a round milestone or an approved record.

Fires when the gap ``G = M − T`` is ≤ min($10M, 5% of M) [CAL], T is verified and fresh
(unchanged for 0 days), and the pace closes the gap within 7 days [CAL]. If M equals a
film's lifetime gross it is a Chase instead. An unchanged gap is suppressed until it has
shrunk by ≥ 50% [CAL] since the last Countdown on the same target (``prior_gaps``).

Negative cases: ``already_crossed``, ``gap_too_large``, ``stale_figure``, ``no_pace``,
``pace_insufficient``, ``gap_not_shrunk``, ``suppressed``.
"""

from __future__ import annotations

from datetime import date, timedelta

from faramir.config import MILESTONE_THRESHOLDS_USD
from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, axis_source, film_ref, money, pace, series, threshold_strength

MAX_GAP_USD = 10_000_000
MAX_GAP_PCT = 0.05
MAX_DAYS = 7
SHRINK_REQUIRED = 0.5


def detect(film: dict, data_date: str, thresholds: list[int] | None = None, prior_gaps: dict | None = None,
           observations: list[dict] | None = None, suppressed_axes: set | None = None) -> DetectorResult:
    """``prior_gaps``: {(axis, M): last delivered gap}; ``suppressed_axes``: {axis} recently suggested."""
    out = DetectorResult()
    prior_gaps = prior_gaps or {}
    suppressed_axes = suppressed_axes or set()
    for axis in ("domestic", "worldwide"):
        prev, cur, stale = series(film, axis)
        if cur <= 0:
            continue
        for m in (thresholds or MILESTONE_THRESHOLDS_USD):
            target = {"type": "threshold", "value": int(m)}
            gap = m - cur
            if gap <= 0:
                continue  # already crossed: Overtake territory (not logged for every lower threshold)
            limit = min(MAX_GAP_USD, MAX_GAP_PCT * m)
            if gap > limit:
                if gap <= 2 * limit:
                    out.reject("countdown", film, axis, target, "gap_too_large", f"{money(gap)} > {money(limit)}")
                continue
            if stale > 0:
                out.reject("countdown", film, axis, target, "stale_figure", f"{axis} figure unchanged for {stale} days")
                continue
            if axis in suppressed_axes:
                out.reject("countdown", film, axis, target, "suppressed", "milestone suggested within the suppression window")
                continue
            per_day, basis = pace(film, axis, observations)
            if per_day is None:
                out.reject("countdown", film, axis, target, "no_pace", "no measurable pace")
                continue
            days = gap / per_day
            if days > MAX_DAYS:
                out.reject("countdown", film, axis, target, "pace_insufficient", f"≈ {days:.0f} days > {MAX_DAYS}")
                continue
            last_gap = prior_gaps.get((axis, int(m)))
            if last_gap is not None and gap > SHRINK_REQUIRED * last_gap:
                out.reject("countdown", film, axis, target, "gap_not_shrunk",
                           f"gap {money(gap)} has not halved since the last Countdown ({money(last_gap)})")
                continue
            projected = (date.fromisoformat(data_date) + timedelta(days=max(1, round(days)))).isoformat()
            out.emit(Event(
                strategy="countdown", film=film_ref(film), period=data_date, axis=axis, target=target,
                status="anticipated",
                evidence={"current": cur, "target_value": float(m), "gap": gap, "gap_limit": limit,
                          "pace_per_day": round(per_day), "pace_basis": basis, "days_to_cross": round(days, 1),
                          "projected_cross_date": projected, "previous_gap": last_gap},
                sources=[axis_source(film, axis, data_date)],
                reason=(f"{film_ref(film)['title']} is {money(gap)} short of {money(m)} {axis} (limit {money(limit)}); "
                        f"at {money(per_day)}/day ({basis.replace('_', ' ')}) it crosses in about {days:.0f} days."),
                reason_codes=["gap_within_limit", f"pace:{basis}"] + (["gap_halved_since_last"] if last_gap else []),
                strength=threshold_strength(m) - 0.05,
                candidate={"kind": "milestone", "axis": axis, "threshold_value": int(m), "milestone_type": "about_to_pass",
                           "angle_categories": ["round_number_milestone"], "comp": None, "suppressed": False,
                           "richness_score": 9}))
    return out
