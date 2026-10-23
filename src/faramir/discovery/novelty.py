"""Novelty and ``similar_prior`` links: overlap of pattern tags and metric with earlier items."""

from __future__ import annotations

from faramir.discovery.items import DiscoveryItem

SIMILAR_AT = 0.5


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def link(item: DiscoveryItem, prior: list[dict]) -> DiscoveryItem:
    sims = []
    for p in prior:
        if p.get("id") == item.id:
            continue
        s = jaccard(item.pattern_tags, p.get("pattern_tags") or []) + (0.25 if p.get("metric") == item.metric else 0)
        s = min(1.0, s)
        if s >= SIMILAR_AT:
            sims.append({"id": p["id"], "period": p.get("period"), "title": p.get("title"), "similarity": round(s, 2)})
    item.similar_prior = sorted(sims, key=lambda x: -x["similarity"])[:5]
    item.novelty = round(1 - (item.similar_prior[0]["similarity"] if item.similar_prior else 0.0), 2)
    return item
