"""Hold (strategy 2): a leader keeps its position against a real challenger.

Fires when F's rank is ≤ R (default 1) on both W−1 and W, and a challenger C opened on W in
≥ 2,500 theaters [CAL] or with ≥ 50% of F's W−1 gross [CAL], and C ranks below F.

Negative cases: ``no_challenger`` (that is a Streak), ``challenger_outranks`` (the opener beat
F), ``limited_opener`` (small release), ``not_held`` (F rose this weekend: a Takeover, not a
Hold), ``chart_unavailable``.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref, money
from faramir.strategies.weekend import Weekend, is_opener

MAX_RANK = 1
CHALLENGER_THEATERS = 2500
CHALLENGER_GROSS_SHARE = 0.5
TIE_BAND = 0.02


def detect(w: Weekend | None, prior: Weekend | None, max_rank: int = MAX_RANK) -> DetectorResult:
    out = DetectorResult()
    if w is None or prior is None:
        out.rejected.append({"strategy": "hold", "film_key": None, "film": None, "axis": "weekend", "target": "rank",
                             "reason_code": "chart_unavailable", "detail": "a weekend chart is not a complete committed collection"})
        return out
    before = prior.by_key()
    openers = [r for r in w.rows if is_opener(r, prior)]
    for f in sorted((r for r in w.rows if r.source_rank and r.source_rank <= max_rank), key=lambda r: r.source_rank):
        target = {"type": "rank", "value": f.source_rank}
        p = before.get(f.film_key)
        if p is None or not p.source_rank or p.source_rank > max_rank:
            out.reject("hold", f.film(), "weekend", target, "not_held",
                       f"{f.title} was not in the top {max_rank} last weekend (a Takeover or climb, not a Hold)")
            continue
        qualifying, notes = [], []
        for c in openers:
            big = (c.theaters or 0) >= CHALLENGER_THEATERS or (p.gross and (c.gross or 0) >= CHALLENGER_GROSS_SHARE * p.gross)
            if not big:
                notes.append(f"{c.title}: limited ({c.theaters or 0} theaters, {money(c.gross or 0)})")
                continue
            if c.source_rank is not None and c.source_rank < f.source_rank:
                out.reject("hold", f.film(), "weekend", target, "challenger_outranks", f"{c.title} opened above {f.title}")
                qualifying = None
                break
            qualifying.append(c)
        if qualifying is None:
            continue
        if not qualifying:
            out.reject("hold", f.film(), "weekend", target, "no_challenger" if not notes else "limited_opener",
                       "; ".join(notes) or "no film opened against it (a Streak, not a Hold)")
            continue
        c = max(qualifying, key=lambda r: r.gross or 0)
        out.emit(Event(
            strategy="hold", film=film_ref(f.film()), period=w.weekend_start, axis="weekend", target=target,
            # Same tie rule as Takeover: within 2% of the challenger on a provisional chart, the order may flip.
            status=("provisional" if w.collection_status != "confirmed_by_recollection" and f.gross and c.gross is not None
                    and (f.gross - c.gross) / f.gross < TIE_BAND else "confirmed"),
            evidence={"rank": f.source_rank, "previous_rank": p.source_rank, "weekend_gross": f.gross,
                      "previous_weekend_gross": p.gross, "challenger": c.title, "challenger_key": c.film_key,
                      "challenger_rank": c.source_rank, "challenger_gross": c.gross, "challenger_theaters": c.theaters,
                      "collection_status": w.collection_status},
            sources=[w.source(), prior.source()],
            reason=(f"{f.title} held No. {f.source_rank} on the {w.weekend_start} weekend ({money(f.gross)}) against "
                    f"{c.title}, which opened in {c.theaters:,} theaters with {money(c.gross)} at No. {c.source_rank}."),
            reason_codes=["held_position", "qualifying_challenger"],
            strength=round(min(1.0, 0.7 + (0.1 if (c.theaters or 0) >= 3500 else 0)), 3)))
    return out
