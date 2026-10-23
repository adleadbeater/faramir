"""Distribution explainer (plan §6): a deterministic funnel per strategy, built only from ledger
tables, with the drop reasons at each stage.

    detected → qualified → deduped → ranked → suggested → delivered → accepted → published → outcome

"Why more X than Y?" is answered by the first stage where their counts diverge. A difference
that is already there at ``detected`` is supply, and is never reported as performance.
"""

from __future__ import annotations

from collections import Counter

from faramir.reports.editorial_report import latest_status

STAGES = ["detected", "qualified", "deduped", "ranked", "suggested", "delivered", "accepted", "published", "outcome"]


def funnel(ledger, outcome_urls: set | None = None) -> dict:
    events = ledger.read("events")
    decisions = ledger.read("decisions")
    deliveries = ledger.read("deliveries")
    status = latest_status(ledger.read("editorial"))
    f: dict[str, Counter] = {}
    drops: dict[str, dict[str, Counter]] = {}

    def add(strategy, stage, n=1):
        f.setdefault(strategy, Counter())[stage] += n

    def drop(strategy, stage, codes):
        for c in codes or ["unspecified"]:
            drops.setdefault(strategy, {}).setdefault(stage, Counter())[c] += 1

    for e in events:
        s = e["strategy"]
        add(s, "detected")
        disp = e.get("disposition")
        if disp in ("primary", "suppressed", "weekend_detected"):
            add(s, "qualified")
        else:
            drop(s, "qualified", (e.get("reason_codes") if disp == "deferred" else None) or [disp or "unclassified"])
        if disp in ("primary", "weekend_detected"):
            add(s, "deduped")
        elif disp == "suppressed":
            drop(s, "deduped", ["already_delivered_or_unchanged"])
    versions, explore = Counter(), Counter()
    for d in decisions:
        s = d["strategy_primary"]
        add(s, "ranked")
        versions[d.get("priority_config_version")] += 1
        if (d.get("disposition") or {}).get("value") == "suggested":
            add(s, "suggested")
            if (d.get("exploration") or {}).get("flag"):
                explore[s] += 1
        else:
            drop(s, "suggested", (d.get("disposition") or {}).get("reason_codes"))
    for dl in deliveries:
        s = dl["strategy"]
        add(s, "delivered")
        st = (status.get(dl.get("suggestion_id") or "") or {})
        if st.get("status") in ("accepted", "assigned", "published"):
            add(s, "accepted")
        elif st.get("status") == "rejected":
            drop(s, "accepted", st.get("reason_codes"))
        if st.get("status") == "published":
            add(s, "published")
            if outcome_urls and st.get("published_url") in outcome_urls:
                add(s, "outcome")
    return {"funnel": {s: {stage: c.get(stage, 0) for stage in STAGES} for s, c in sorted(f.items())},
            "drops": {s: {st: dict(sorted(c.items())) for st, c in v.items()} for s, v in sorted(drops.items())},
            "priority_config_versions": dict(versions), "exploration_suggested": dict(explore)}


def why_more(report: dict, a: str, b: str) -> str:
    fa, fb = report["funnel"].get(a, {}), report["funnel"].get(b, {})
    for stage in STAGES:
        ra, rb = fa.get(stage, 0), fb.get(stage, 0)
        if ra != rb:
            if stage == "detected":
                return (f"{a} vs {b}: the difference is supply — {ra} vs {rb} events detected. "
                        "It says nothing about performance.")
            return (f"{a} vs {b}: equal through the earlier stages; they diverge at '{stage}' ({ra} vs {rb}). "
                    f"Drop reasons at that stage: {a} {report['drops'].get(a, {}).get(stage, {})}, "
                    f"{b} {report['drops'].get(b, {}).get(stage, {})}.")
    return f"{a} and {b} have identical funnels."
