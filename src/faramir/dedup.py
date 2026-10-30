"""Event-level dedup against what was already delivered (plan §3 rule 1, Phase 1 acceptance).

* A confirmed event whose story (strategy, film, axis, target) was delivered before is
  ``already_delivered`` — a film passes a given film or threshold once.
* An anticipated event whose story was delivered within the last 14 days [CAL] is
  ``unchanged_anticipated`` unless it moved materially since then: the remaining gap has at
  least halved [CAL].
* Within a run, two clusters can never share a primary event (the classifier guarantees
  one per cluster); identical story keys across clusters keep the stronger one.
"""

from __future__ import annotations

from datetime import date

from faramir.classify import Cluster

ANTICIPATED_WINDOW_DAYS = 14
MATERIAL_GAP_SHRINK = 0.5


def dedup(clusters: list[Cluster], deliveries: list[dict], run_date: str) -> tuple[list[Cluster], list[dict]]:
    by_story: dict[str, list[dict]] = {}
    for d in deliveries:
        by_story.setdefault(d["story_key"], []).append(d)
    kept, suppressed, seen = [], [], {}
    for c in sorted(clusters, key=lambda c: -c.primary.strength):
        e = c.primary
        prior = sorted(by_story.get(e.story_key, []), key=lambda d: d["period"])
        if e.story_key in seen:
            suppressed.append({"event_id": e.event_id, "reason_code": "duplicate_story_in_run", "kept": seen[e.story_key]})
            continue
        if prior and e.status == "confirmed":
            suppressed.append({"event_id": e.event_id, "reason_code": "already_delivered", "delivered": prior[-1]["period"]})
            continue
        if prior and e.status == "anticipated":
            last = prior[-1]
            age = (date.fromisoformat(run_date) - date.fromisoformat(last["period"])).days
            last_gap, gap = (last.get("evidence") or {}).get("gap"), e.evidence.get("gap")
            moved = last_gap and gap is not None and gap <= MATERIAL_GAP_SHRINK * last_gap
            if age <= ANTICIPATED_WINDOW_DAYS and not moved:
                suppressed.append({"event_id": e.event_id, "reason_code": "unchanged_anticipated",
                                   "delivered": last["period"]})
                continue
        seen[e.story_key] = e.event_id
        kept.append(c)
    return kept, suppressed
