from __future__ import annotations
"""Round-number milestone detection for Faramir."""

from faramir.config import MILESTONE_THRESHOLDS_USD, MILESTONE_PROXIMITY_PCT


def find_milestones(active_film: dict, thresholds: list[int] | None = None, proximity_pct: float | None = None) -> list[dict]:
    """Detect just-passed and about-to-pass milestones for an active film.

    Checks both domestic and worldwide axes.

    Returns a list of milestone candidate dicts, one per axis per threshold hit.
    """
    if thresholds is None:
        thresholds = MILESTONE_THRESHOLDS_USD
    if proximity_pct is None:
        proximity_pct = MILESTONE_PROXIMITY_PCT

    milestones = []

    axes = [
        ("domestic", active_film.get("domestic_today", 0), active_film.get("domestic_yesterday", 0)),
        ("worldwide", active_film.get("worldwide_today", 0), active_film.get("worldwide_yesterday", 0)),
    ]

    for axis, today, yesterday in axes:
        if not today:
            continue
        for threshold in thresholds:
            low_band = threshold * (1 - proximity_pct)

            just_passed = today >= threshold and yesterday < threshold
            about_to_pass = today >= low_band and today < threshold

            if just_passed:
                milestones.append({
                    "kind": "milestone",
                    "axis": axis,
                    "threshold_value": threshold,
                    "angle_categories": ["round_number_milestone"],
                    "richness_score": 9,
                    "suppressed": False,
                    "comp": None,
                    "milestone_type": "just_passed",
                })
            elif about_to_pass:
                milestones.append({
                    "kind": "milestone",
                    "axis": axis,
                    "threshold_value": threshold,
                    "angle_categories": ["round_number_milestone"],
                    "richness_score": 9,
                    "suppressed": False,
                    "comp": None,
                    "milestone_type": "about_to_pass",
                })

    return milestones
