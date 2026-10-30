"""Streak (strategy 3): F stays within tier T for N complete, consecutive weekends.

Tiers [CAL]: No. 1 for N ≥ 3, top 3 for N ≥ 4, top 5 for N ≥ 6. The highest qualifying tier is
used. Repeats only at noteworthy extensions: the minimum N, then every 2 more weekends [CAL].
A missing weekend (no committed collection) inside the run makes the event ``unverifiable`` —
the streak is never assumed to continue; a weekend where F is outside the tier (or off the chart)
breaks it.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref
from faramir.strategies.weekend import Weekend

TIERS = [(1, 3), (3, 4), (5, 6)]
EXTENSION_STEP = 2


def _run_length(series: list[Weekend | None], key: str, tier: int) -> tuple[int, bool]:
    """(consecutive weekends within tier, ending now; whether a missing weekend interrupted the count)."""
    n = 0
    for w in reversed(series):
        if w is None:
            return n, True
        r = w.by_key().get(key)
        if r is None or not r.source_rank or r.source_rank > tier:
            return n, False
        n += 1
    return n, False


def detect(series: list[Weekend | None]) -> DetectorResult:
    out = DetectorResult()
    cur = series[-1] if series else None
    if cur is None:
        return out
    for r in cur.rows:
        if not r.source_rank or r.source_rank > TIERS[-1][0]:
            continue
        best = None
        for tier, minimum in TIERS:
            n, gap = _run_length(series, r.film_key, tier)
            if n >= minimum:
                best = (tier, minimum, n, gap)
                break
        target = {"type": "streak", "value": None}
        if best is None:
            tier, minimum = next((t, m) for t, m in TIERS if r.source_rank <= t)
            n, gap = _run_length(series, r.film_key, tier)
            if gap and n < minimum:
                out.reject("streak", r.film(), "weekend", {"type": "streak", "value": f"top{tier}"}, "unverifiable_gap",
                           f"{n} weekends in the top {tier}, then a weekend with no committed chart")
            continue
        tier, minimum, n, gap = best
        noteworthy = n == minimum or (n - minimum) % EXTENSION_STEP == 0
        target = {"type": "streak", "value": f"top{tier}:{n}"}
        if not noteworthy:
            out.reject("streak", r.film(), "weekend", target, "not_a_noteworthy_extension",
                       f"{n} weekends (reported at {minimum}, then every {EXTENSION_STEP})")
            continue
        label = "at No. 1" if tier == 1 else f"in the top {tier}"
        out.emit(Event(
            strategy="streak", film=film_ref(r.film()), period=cur.weekend_start, axis="weekend", target=target,
            status="confirmed",  # every counted weekend is a committed chart; a gap stops the count
            evidence={"tier": tier, "weekends": n, "minimum": minimum, "rank": r.source_rank, "weekend_gross": r.gross,
                      "weekends_checked": [w.weekend_start for w in series[-n:] if w is not None]},
            sources=[w.source() for w in reversed(series[-n:]) if w is not None],  # every weekend of the run, latest first
            reason=f"{r.title} has been {label} for {n} consecutive complete weekends (through {cur.weekend_start}).",
            reason_codes=[f"tier_top{tier}", f"weekends_{n}"], subtype=f"top{tier}",
            strength=round(min(1.0, 0.6 + (0.15 if tier == 1 else 0.05) + 0.03 * (n - minimum)), 3)))
    return out
