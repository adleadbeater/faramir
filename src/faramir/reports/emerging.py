"""Emerging Strategies Report (plan §4): which Discovery patterns have earned a look as a new
strategy. A cluster of accepted Discovery items sharing an **editor-confirmed** tag becomes a
*candidate* when all of these hold [CAL]:

* ≥ 5 accepted items across ≥ 3 films;
* ≥ 3 published;
* 14-day sessions per article ≥ the box-office cohort median (needs age-comparable outcomes,
  Phase 4; ``pending_outcomes`` until they exist);
* a deterministic rule has been drafted (listed in ``drafted_rules``).

Promotion is never automatic: the report lists candidates and their unmet criteria; adding a
strategy is a versioned config change someone approves.
"""

from __future__ import annotations

MIN_ACCEPTED = 5
MIN_FILMS = 3
MIN_PUBLISHED = 3


def build(ledger, drafted_rules: set[str] | None = None, outcomes: dict | None = None,
          cohort_median_14d: float | None = None) -> dict:
    items = {r["id"]: r for r in ledger.read("discovery")}
    status: dict[str, dict] = {}
    for r in sorted(ledger.read("discovery_editorial"), key=lambda r: (r.get("observed") or "", r["id"])):
        status[r["discovery_id"]] = r
    clusters: dict[str, list[dict]] = {}
    for did, st in status.items():
        item = items.get(did)
        if not item:
            continue
        for tag in st.get("confirmed_tags") or []:
            clusters.setdefault(tag, []).append({"item": item, "status": st})
    out = {}
    for tag, members in sorted(clusters.items()):
        accepted = [m for m in members if m["status"]["status"] in ("accepted", "assigned", "published")]
        published = [m for m in members if m["status"]["status"] == "published"]
        films = {m["item"].get("film_key") for m in accepted if m["item"].get("film_key")}
        urls = [m["status"].get("published_url") for m in published if m["status"].get("published_url")]
        sess = [outcomes[u] for u in urls if outcomes and u in outcomes]
        perf = ("pending_outcomes" if not sess or cohort_median_14d is None
                else "met" if sorted(sess)[len(sess) // 2] >= cohort_median_14d else "not_met")
        criteria = {"accepted_items": (len(accepted), len(accepted) >= MIN_ACCEPTED),
                    "films": (len(films), len(films) >= MIN_FILMS),
                    "published": (len(published), len(published) >= MIN_PUBLISHED),
                    "sessions_14d_vs_cohort_median": perf,
                    "rule_drafted": bool(drafted_rules and tag in drafted_rules)}
        candidate = all(v[1] for k, v in criteria.items() if isinstance(v, tuple)) and perf == "met" and criteria["rule_drafted"]
        out[tag] = {"criteria": criteria, "candidate": candidate,
                    "members": sorted(m["item"]["id"] for m in members),
                    "unmet": [k for k, v in criteria.items()
                              if (isinstance(v, tuple) and not v[1]) or v in (False, "not_met", "pending_outcomes")]}
    return {"patterns": out, "promotion": "never automatic — a candidate needs an approved config change"}
