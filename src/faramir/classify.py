"""Classification and overlap resolution (plan §3 "Overlap resolution").

Events are clustered by ``(film, period, axis, target)``; a Chase and a Countdown on the
same film/period/axis whose targets are within 2% of each other join one cluster (rule 2).
Each cluster yields one primary and at most one secondary strategy. Every other event in
the cluster is recorded in ``rejected_alternatives`` with a reason code (rule 6).

Ordering inside a cluster: overlap rules first (later phases register more), then status
(confirmed > anticipated > provisional > unverifiable: rule 1, completed beats anticipated),
then strength, then the fixed strategy order. A cluster whose best event is provisional or
unverifiable is ``deferred`` — never delivered.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faramir.events.schema import DELIVERABLE, STATUS_RANK, STRATEGIES, Event

TARGET_BAND = 0.02
OVERLAP_RULES: list = []  # callables(events) -> (ordered events, {event_id: reason_code}) for phase-specific rules


@dataclass
class Cluster:
    key: tuple
    primary: Event
    secondary: Event | None = None
    rejected_alternatives: list[dict] = field(default_factory=list)
    disposition: str = "qualified"          # qualified | deferred
    reason_codes: list[str] = field(default_factory=list)

    @property
    def events(self) -> list[Event]:
        return [self.primary] + ([self.secondary] if self.secondary else [])


def _target_value(e: Event) -> float | None:
    v = e.target.get("value") if e.target.get("type") == "threshold" else e.evidence.get("target_value")
    return float(v) if v else None


def _merge_bands(groups: dict[tuple, list[Event]]) -> list[list[Event]]:
    """Rule 2: Chase (comparison) and Countdown (threshold) within 2% on the same film/period/axis share a cluster."""
    keys = list(groups)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[:3] != b[:3]:
                continue
            ea, eb = groups[a][0], groups[b][0]
            if {ea.strategy, eb.strategy} != {"chase", "countdown"}:
                continue
            va, vb = _target_value(ea), _target_value(eb)
            if va and vb and abs(va - vb) / max(va, vb) <= TARGET_BAND:
                parent[find(b)] = find(a)
    merged: dict[tuple, list[Event]] = {}
    for k in keys:
        merged.setdefault(find(k), []).extend(groups[k])
    return list(merged.values())


def _order(events: list[Event]) -> tuple[list[Event], dict[str, str]]:
    reasons: dict[str, str] = {}
    for rule in OVERLAP_RULES:
        events, more = rule(events)
        reasons.update(more)
    ranked = sorted(events, key=lambda e: (STATUS_RANK[e.status], -e.strength, STRATEGIES.index(e.strategy)))
    return ranked, reasons


def classify(events: list[Event]) -> list[Cluster]:
    groups: dict[tuple, list[Event]] = {}
    for e in events:
        groups.setdefault(e.cluster_key, []).append(e)
    clusters = []
    for members in _merge_bands(groups):
        ranked, rule_reasons = _order(members)
        primary = ranked[0]
        cluster = Cluster(key=primary.cluster_key, primary=primary)
        for e in ranked[1:]:
            if rule_reasons.get(e.event_id):
                code = rule_reasons[e.event_id]
            elif e.strategy == primary.strategy:
                code = "duplicate_in_cluster"
            elif STATUS_RANK[e.status] > STATUS_RANK[primary.status]:
                code = "completed_beats_anticipated" if primary.status == "confirmed" else "lower_status"
            elif {e.strategy, primary.strategy} == {"chase", "countdown"}:
                code = "chase_countdown_same_band_lower_strength"
            else:
                code = "lower_strength"
            if cluster.secondary is None and e.strategy != primary.strategy and e.status in DELIVERABLE \
                    and code not in ("completed_beats_anticipated",):
                cluster.secondary = e
                continue
            cluster.rejected_alternatives.append({"event_id": e.event_id, "strategy": e.strategy, "reason_code": code})
        if primary.status not in DELIVERABLE:
            cluster.disposition = "deferred"
            cluster.reason_codes.append(f"primary_{primary.status}")
        clusters.append(cluster)
    return clusters
