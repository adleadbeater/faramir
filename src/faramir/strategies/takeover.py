"""Takeover (strategy 1): a new No. 1 on the weekend chart.

Fires when F ranks 1 on weekend W, a different film G ranked 1 on W−1, and both charts are
complete committed collections. Subtypes: ``debut`` (F opened on W) and ``climb``.
Tie rule: if F and the No. 2 film are within 2% [CAL] while W is still provisional, the event
is ``provisional`` and is not delivered.

Negative cases: F was No. 1 on both weekends (``same_leader``); either chart missing or
incomplete (``chart_unavailable``); no film ranked 1 (``no_ranked_leader``). Daily charts never
enter weekend history, so a daily-chart No. 1 can never trigger it.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref, money
from faramir.strategies.weekend import Weekend, is_opener

TIE_BAND = 0.02


def detect(w: Weekend | None, prior: Weekend | None, period: str | None = None) -> DetectorResult:
    out = DetectorResult()
    target = {"type": "rank", "value": 1}
    if w is None or prior is None:
        missing = "this weekend" if w is None else "last weekend"
        out.rejected.append({"strategy": "takeover", "film_key": None, "film": None, "axis": "weekend",
                             "target": "rank:1", "reason_code": "chart_unavailable",
                             "detail": f"{missing}'s chart is not a complete committed collection"})
        return out
    f, g = w.ranked(1), prior.ranked(1)
    if f is None or g is None:
        out.rejected.append({"strategy": "takeover", "film_key": None, "film": None, "axis": "weekend", "target": "rank:1",
                             "reason_code": "no_ranked_leader", "detail": "a chart has no film ranked 1"})
        return out
    if f.film_key == g.film_key:
        out.reject("takeover", f.film(), "weekend", target, "same_leader", f"{f.title} was also No. 1 last weekend")
        return out
    second = w.ranked(2)
    margin = (f.gross - second.gross) / f.gross if second and f.gross and second.gross is not None else None
    provisional = w.collection_status != "confirmed_by_recollection" and margin is not None and margin < TIE_BAND
    debut = is_opener(f, prior)
    prior_rank = prior.by_key().get(f.film_key).source_rank if f.film_key in prior.by_key() else None
    reason = (f"{f.title} is No. 1 on the {w.weekend_start} weekend chart ({money(f.gross)}), taking over from "
              f"{g.title} (No. 1 on {prior.weekend_start})"
              + (", in its opening weekend." if debut else f", climbing from No. {prior_rank or 'unranked'}.")
              + (f" Margin over No. 2 is {margin:.1%} on a provisional chart: provisional." if provisional else ""))
    out.emit(Event(
        strategy="takeover", film=film_ref(f.film()), period=w.weekend_start, axis="weekend", target=target,
        status="provisional" if provisional else "confirmed",
        evidence={"rank": 1, "weekend_gross": f.gross, "theaters": f.theaters, "previous_leader": g.title,
                  "previous_leader_key": g.film_key, "previous_rank": prior_rank,
                  "second_place": second.title if second else None, "second_gross": second.gross if second else None,
                  "margin_over_second": round(margin, 4) if margin is not None else None,
                  "collection_status": w.collection_status},
        sources=[w.source(), prior.source()], reason=reason,
        reason_codes=["new_number_one", "debut" if debut else "climb"] + (["near_tie_provisional"] if provisional else []),
        subtype="debut" if debut else "climb", strength=0.85 if debut else 0.8))
    return out
