"""Run the weekend strategies over committed weekend history.

With weekend-history collection disabled (PR-16) there is no history, and the result says so
(``history_unavailable``) instead of producing anything. Every event and rejection is written
to the ledger like the daily strategies'.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faramir.classify import classify
from faramir.events.ledger import Ledger
from faramir.strategies import dark_horse, enabled, exit as exit_strategy, hold, legs, registry, streak, takeover
from faramir.strategies.common import DetectorResult
from faramir.strategies.weekend import Weekend, from_snapshot, previous_friday

LOOKBACK_WEEKS = 8
# Every weekend detector takes the consecutive series (oldest → newest; None = no committed chart).
WEEKEND_DETECTORS = {
    "takeover": lambda s, ctx: takeover.detect(s[-1], s[-2] if len(s) > 1 else None),
    "hold": lambda s, ctx: hold.detect(s[-1], s[-2] if len(s) > 1 else None),
    "streak": lambda s, ctx: streak.detect(s),
    "exit": lambda s, ctx: exit_strategy.detect(s),
    "legs": lambda s, ctx: legs.detect(s, ctx.get("history"), ctx.get("genres")),
    "dark_horse": lambda s, ctx: dark_horse.detect(s, ctx.get("metadata")),
}


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


def run_on_series(series: list[Weekend | None], weekend_start: str, reg: dict | None = None,
                  context: dict | None = None) -> WeekendResult:
    reg = reg or registry()
    found = DetectorResult()
    for name, fn in WEEKEND_DETECTORS.items():
        if enabled(name, reg):
            found.extend(fn(series, context or {}))
    ok = len(series) >= 2 and series[-1] is not None and series[-2] is not None
    status = "ok" if ok else "history_unavailable"
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
    series, friday = [], weekend_start
    for _ in range(LOOKBACK_WEEKS):
        series.insert(0, from_snapshot(history.read_weekend(gc, sheet_id, friday, log)))
        friday = previous_friday(friday)
    while series and series[0] is None:
        series.pop(0)   # leading weeks before history began are not gaps
    if not series:
        series = [None]
    weekends = {e["weekend_start"] for e in log.events if e["event"] == "commit"}
    history_all = [from_snapshot(history.read_weekend(gc, sheet_id, f, log)) for f in sorted(weekends)]
    result = run_on_series(series, weekend_start, reg, {"history": [w for w in history_all if w is not None]})
    if ledger is not None:
        ledger.append("events", [{**e.to_record(), "disposition": "weekend_detected"} for e in result.events],
                      month=weekend_start[:7])
    return result
