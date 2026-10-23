"""Run Discovery over committed weekend history: generators + open pass → verification →
exclusion of anomalies a strategy event already explains → novelty → ledger (``discovery``).

Off with weekend-history collection (PR-16): returns ``history_collection_disabled``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from faramir.discovery import generators as G
from faramir.discovery import novelty, open_pass
from faramir.discovery.items import DiscoveryItem, verify
from faramir.events.ledger import Ledger
from faramir.strategies.weekend import Weekend, from_snapshot

LOOKBACK_WEEKS = 6
# A strategy event "explains" an anomaly on the same film and period with these metrics.
EXPLAINED_BY = {"source_rank": {"takeover", "hold", "streak"}, "weekend_change_pct": {"legs", "hold"},
                "theater_change_pct": {"exit"}, "weekend_gross": {"takeover", "dark_horse"}}


@dataclass
class DiscoveryResult:
    status: str
    weekend_start: str
    items: list[DiscoveryItem] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"status": self.status, "weekend_start": self.weekend_start,
                "items": [f"{i.generator}:{i.title or 'chart'}" for i in self.items],
                "dropped_unverified": len(self.dropped), "explained_by_strategy": len(self.excluded)}


def discover(weeks: list[Weekend], strategy_events: list | None = None, metadata: dict | None = None,
             proposer=None, prior: list[dict] | None = None) -> DiscoveryResult:
    """weeks: consecutive committed weekends, oldest → newest (the last one is analysed)."""
    if not weeks:
        return DiscoveryResult("history_unavailable", "")
    cur = weeks[-1]
    by_period = {w.weekend_start: w for w in weeks}
    candidates = (G.comeback(weeks) + G.rerelease_share(cur) + G.per_theater_outlier(cur)
                  + G.weekend_change_zscore(weeks) + G.expansion_surge(weeks) + G.format_contrast(cur, metadata or {}))
    result = DiscoveryResult("ok", cur.weekend_start)
    summary = {"weekend_start": cur.weekend_start, "rows": [r.__dict__ for r in cur.rows]}
    proposed, dropped = open_pass.run(proposer or open_pass.NullOpenPass(), summary, by_period)
    result.dropped += dropped
    explained = {(getattr(e, "film", {}).get("tn_slug"), e.period, e.strategy) for e in (strategy_events or [])}
    for item in candidates + proposed:
        item = verify(item, by_period) if item.generator != "open_pass" else item
        if item.status != "proposed":
            result.dropped.append({"id": item.id, "generator": item.generator, "reason": "; ".join(item.verification)})
            continue
        why = [s for s in EXPLAINED_BY.get(item.metric, set()) if (item.film_key, item.period, s) in explained]
        if why:
            result.excluded.append({"id": item.id, "generator": item.generator, "explained_by": why})
            continue
        result.items.append(novelty.link(item, prior or []))
    return result


def run(gc, sheet_id: str, weekend_start: str, ledger: Ledger | None = None, strategy_events=None,
        collection_enabled: bool | None = None, proposer=None) -> DiscoveryResult:
    from faramir import history

    if collection_enabled is None:
        collection_enabled = history.collection_enabled()
    if not collection_enabled:
        return DiscoveryResult("history_collection_disabled", weekend_start)
    log = history.read_log(gc, sheet_id)
    weeks = []
    for i in range(LOOKBACK_WEEKS - 1, -1, -1):
        friday = (date.fromisoformat(weekend_start) - timedelta(weeks=i)).isoformat()
        w = from_snapshot(history.read_weekend(gc, sheet_id, friday, log))
        if w is None:
            weeks = []          # a gap breaks the consecutive series: analyse only what follows it
            continue
        weeks.append(w)
    if not weeks or weeks[-1].weekend_start != weekend_start:
        return DiscoveryResult("history_unavailable", weekend_start)
    prior = ledger.read("discovery") if ledger else []
    result = discover(weeks, strategy_events, None, proposer, prior)
    if ledger is not None:
        ledger.append("discovery", [i.to_record() for i in result.items], month=weekend_start[:7])
        ledger.append("discovery_dropped", [{"id": f"{weekend_start}:{d.get('id') or d.get('pattern')}", **d}
                                            for d in result.dropped], month=weekend_start[:7])
    return result
