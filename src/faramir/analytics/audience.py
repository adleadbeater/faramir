"""Audience affinity from the sanitized Cerebro snapshot (replaces the live-sheet signals of
``faramir.cerebro`` when ``FARAMIR_CEREBRO_SNAPSHOT`` is configured; plan §2, §5).

For a subject (talent name, franchise, film title): MovieWeb News-team articles about it,
matured, published within the lookback window — their cumulative sessions per article against
the same window's News pool. Same shape as the legacy signals ({name: {..., is_signal}}) plus the
evidence: n, median, pool median, lift, tier. Brands and team pools are never mixed; cumulative
sessions are reported as such.
"""

from __future__ import annotations

import statistics
from datetime import timedelta

from faramir.analytics.cerebro_snapshot import Snapshot, matches

LOOKBACK_DAYS = 180
MIN_ARTICLES = 3
SIGNAL_LIFT = 1.25     # median ≥ 1.25 × pool median [CAL]
HIGH_LIFT = 2.0


def _window(snap: Snapshot, team: str, min_age_days: int):
    start = snap.as_of - timedelta(days=LOOKBACK_DAYS)
    return [a for a in snap.pool(team, min_age_days) if a.pub_date.date() >= start]


def signals(snap: Snapshot, names: list[str], team: str = "news", min_age_days: int = 14) -> dict:
    pool = _window(snap, team, min_age_days)
    pool_median = statistics.median([a.sessions for a in pool]) if pool else None
    out = {}
    for name in names:
        if not name:
            continue
        arts = [a for a in pool if matches(a, [name])]
        med = statistics.median([a.sessions for a in arts]) if arts else None
        lift = (med / pool_median) if med is not None and pool_median else None
        is_signal = len(arts) >= MIN_ARTICLES and lift is not None and lift >= SIGNAL_LIFT
        tier = ("high" if is_signal and lift >= HIGH_LIFT else "medium" if is_signal
                else "low" if len(arts) >= MIN_ARTICLES else "unknown")
        out[name] = {"articles": len(arts), "median_sessions": med, "pool_median_sessions": pool_median,
                     "lift": round(lift, 3) if lift is not None else None, "tier": tier, "is_signal": is_signal,
                     "basis": f"MW {team} pool, matured ≥{min_age_days}d, published in the {LOOKBACK_DAYS} days to "
                              f"{snap.as_of}; cumulative sessions per article"}
    return out


def affinity_score(sig: dict | None) -> float:
    """0–1 audience component for the ranker (plan §6): unknown is neutral (0.5), never invented."""
    if not sig or sig.get("tier") == "unknown":
        return 0.5
    return {"high": 1.0, "medium": 0.75, "low": 0.3}[sig["tier"]]
