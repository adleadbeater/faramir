"""Exit (strategy 6): a sharp theater pull-back, or the end of the run.

Fires when the theater count drops ≥ 40% [CAL] week over week from a base of at least 1,000
[CAL] (subtype ``theater_drop``). End of run (``end_of_run``) is inferred only when the film is
absent from 2 consecutive complete charts after being on one, and its total had stopped
changing. A missing weekend is never read as an absence; a PVOD release is not in the data and
is never claimed.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref, money, scale_strength
from faramir.strategies.weekend import Weekend

DROP_PCT = 40.0
MIN_BASE = 1000
MIN_TOTAL_FOR_END = 10_000_000  # [CAL] end-of-run only for films that mattered


def detect(series: list[Weekend | None]) -> DetectorResult:
    out = DetectorResult()
    if len(series) < 2 or series[-1] is None:
        return out
    cur, prev = series[-1], series[-2]
    if prev is not None:
        cb = cur.by_key()
        for p in prev.rows:
            r = cb.get(p.film_key)
            if r is None or not p.theaters or r.theaters is None:
                continue
            drop = 100 * (p.theaters - r.theaters) / p.theaters
            target = {"type": "theaters", "value": r.theaters}
            if drop < DROP_PCT:
                continue
            if p.theaters < MIN_BASE:
                out.reject("exit", r.film(), "weekend", target, "small_base", f"base {p.theaters} < {MIN_BASE}")
                continue
            out.emit(Event(
                strategy="exit", film=film_ref(r.film()), period=cur.weekend_start, axis="weekend", target=target,
                status="confirmed", subtype="theater_drop",
                evidence={"previous_theaters": p.theaters, "theaters": r.theaters, "drop_pct": round(drop, 1),
                          "weekend_gross": r.gross, "total_gross": r.total_gross},
                sources=[cur.source(), prev.source()],
                reason=f"{r.title} dropped from {p.theaters:,} to {r.theaters:,} theaters ({drop:.0f}%) on the {cur.weekend_start} weekend.",
                reason_codes=["theater_drop"], strength=scale_strength(r.total_gross, 0.55)))
    # End of run: on the chart three weekends ago, absent from the last two complete charts, total stalled.
    if len(series) >= 4 and all(w is not None for w in series[-4:]):
        before, last_on, gone1, gone2 = series[-4], series[-3], series[-2], series[-1]
        for r in last_on.rows:
            b = before.by_key().get(r.film_key)
            if r.film_key in gone1.by_key() or r.film_key in gone2.by_key() or b is None:
                continue
            if (r.total_gross or 0) < MIN_TOTAL_FOR_END:
                continue
            if r.total_gross is not None and b.total_gross is not None and r.total_gross - b.total_gross <= 0.01 * r.total_gross:
                out.emit(Event(
                    strategy="exit", film=film_ref(r.film()), period=gone2.weekend_start, axis="weekend",
                    target={"type": "run", "value": "end"}, status="confirmed", subtype="end_of_run",
                    evidence={"last_charted": last_on.weekend_start, "total_gross": r.total_gross,
                              "absent_from": [gone1.weekend_start, gone2.weekend_start]},
                    sources=[gone2.source(), gone1.source(), last_on.source()],
                    reason=(f"{r.title} has been off two consecutive complete charts since {last_on.weekend_start}, "
                            f"its total having stalled at {money(r.total_gross)}."),
                    reason_codes=["absent_two_complete_charts", "total_stalled"], strength=scale_strength(r.total_gross, 0.5)))
    return out
