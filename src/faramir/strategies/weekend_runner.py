"""Run the weekend strategies over committed weekend history.

With weekend-history collection disabled (PR-16) there is no history, and the result says so
(``history_unavailable``) instead of producing anything. Every event and rejection is written
to the ledger like the daily strategies'.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faramir.classify import classify
from faramir.events.ledger import Ledger
from faramir.strategies import enabled, hold, registry, takeover
from faramir.strategies.common import DetectorResult
from faramir.strategies.weekend import Weekend, from_snapshot, previous_friday

WEEKEND_DETECTORS = {"takeover": takeover.detect, "hold": hold.detect}


@dataclass
class WeekendResult:
    status: str                               # ok | history_unavailable | history_collection_disabled
    weekend_start: str
    events: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    detail: str = ""

    def summary(self) -> dict:
        return {"status": self.status, "weekend_start": self.weekend_start, "detail": self.detail,
                "events": [f"{e.strategy}:{e.film.get('title')}:{e.status}" for e in self.events],
                "rejected": [f"{r['strategy']}:{r['reason_code']}" for r in self.rejected]}


def run_on_weekends(w: Weekend | None, prior: Weekend | None, weekend_start: str, reg: dict | None = None,
                    extra: dict | None = None) -> WeekendResult:
    reg = reg or registry()
    found = DetectorResult()
    for name, fn in {**WEEKEND_DETECTORS, **(extra or {})}.items():
        if enabled(name, reg):
            found.extend(fn(w, prior))
    status = "ok" if w is not None and prior is not None else "history_unavailable"
    return WeekendResult(status, weekend_start, found.events, classify(found.events), found.rejected,
                         "" if status == "ok" else "a required weekend is not a complete committed collection")


def run(gc, sheet_id: str, weekend_start: str, ledger: Ledger | None = None, reg: dict | None = None,
        collection_enabled: bool | None = None) -> WeekendResult:
    from faramir import history

    if collection_enabled is None:
        collection_enabled = history.collection_enabled()
    if not collection_enabled:
        return WeekendResult("history_collection_disabled", weekend_start,
                             detail="FARAMIR_COLLECT_HISTORY is off (PR-16): weekend strategies have no data")
    log = history.read_log(gc, sheet_id)
    w = from_snapshot(history.read_weekend(gc, sheet_id, weekend_start, log))
    prior = from_snapshot(history.read_weekend(gc, sheet_id, previous_friday(weekend_start), log))
    result = run_on_weekends(w, prior, weekend_start, reg)
    if ledger is not None:
        ledger.append("events", [{**e.to_record(), "disposition": "weekend_detected"} for e in result.events],
                      month=weekend_start[:7])
    return result
