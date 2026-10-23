"""Shrinkage estimates (plan §5): empirical Bayes, each level pulled toward its parent with
weight n/(n+k) [k=10, CAL]: strategy × cohort → strategy → global. Every estimate carries n,
an interval and whether it is ``experimental`` (n_eff < 8, then the broader level is used).

Strategy and headline construction are estimated as separate factors (``by="construction"``),
so a weak construction doesn't penalize a strategy. With no outcomes at all the result is
``calibration_pending`` — never a number derived from data Faramir doesn't have.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass
class Estimate:
    scope: str
    n: int
    estimate: float | None
    interval: tuple[float, float] | None
    fallback_level: str | None
    is_experimental: bool
    status: str = "estimated"          # estimated | calibration_pending

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _shrink(values: list[float], parent: float, parent_sd: float, k: float) -> tuple[float, float]:
    n = len(values)
    w = n / (n + k)
    mean = statistics.fmean(values) if values else parent
    est = w * mean + (1 - w) * parent
    sd = statistics.pstdev(values) if n > 1 else 0.0
    sd = sd or parent_sd  # identical values say nothing about spread: use the parent level's
    se = sd / math.sqrt(n + k)
    return est, se


def estimate(rows: list[dict], key: str, cohort_key: str | None = None, k: float = 10, min_n: int = 8,
             metric: str = "sessions") -> dict[str, Estimate]:
    """rows: [{strategy, construction, cohort, sessions, ...}] — outcomes with status ok only."""
    vals = [r for r in rows if r.get(metric) is not None]
    if not vals:
        return {"__global__": Estimate("global", 0, None, None, None, True, "calibration_pending")}
    allv = [float(r[metric]) for r in vals]
    g_mean, g_sd = statistics.fmean(allv), (statistics.pstdev(allv) if len(allv) > 1 else 0.0)
    out = {"__global__": Estimate("global", len(allv), round(g_mean, 1),
                                  (round(g_mean - 1.28 * g_sd / math.sqrt(len(allv)), 1),
                                   round(g_mean + 1.28 * g_sd / math.sqrt(len(allv)), 1)), None, len(allv) < min_n)}
    groups: dict[str, list[dict]] = {}
    for r in vals:
        groups.setdefault(str(r.get(key)), []).append(r)
    for g, members in sorted(groups.items()):
        v = [float(r[metric]) for r in members]
        est, se = _shrink(v, g_mean, g_sd, k)
        exp = len(v) < min_n
        out[g] = Estimate(f"{key}={g}", len(v), round(g_mean if exp else est, 1),
                          (round(est - 1.28 * se, 1), round(est + 1.28 * se, 1)), "global" if exp else None, exp)
        if cohort_key:
            sub: dict[str, list[float]] = {}
            for r in members:
                sub.setdefault(str(r.get(cohort_key)), []).append(float(r[metric]))
            for c, cv in sorted(sub.items()):
                cest, cse = _shrink(cv, est, statistics.pstdev(v) if len(v) > 1 else g_sd, k)
                cexp = len(cv) < min_n
                out[f"{g}|{c}"] = Estimate(f"{key}={g}|{cohort_key}={c}", len(cv), round(est if cexp else cest, 1),
                                           (round(cest - 1.28 * cse, 1), round(cest + 1.28 * cse, 1)),
                                           key if cexp else None, cexp)
    return out
