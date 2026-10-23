"""Editorial status report, reproduced exactly from the ledger (``deliveries`` + ``editorial``)."""

from __future__ import annotations

from collections import Counter


def latest_status(editorial_rows: list[dict]) -> dict[str, dict]:
    """The most recent observed status per suggestion (ties broken by id for determinism)."""
    out: dict[str, dict] = {}
    for r in sorted(editorial_rows, key=lambda r: (r.get("observed") or "", r["id"])):
        out[r["suggestion_id"]] = r
    return out


def build(ledger) -> dict:
    deliveries = ledger.read("deliveries")
    status = latest_status(ledger.read("editorial"))
    by_strategy: dict[str, Counter] = {}
    reasons: dict[str, Counter] = {}
    for d in sorted(deliveries, key=lambda d: d["id"]):
        s = d.get("strategy") or "unknown"
        st = (status.get(d.get("suggestion_id") or "") or {}).get("status", "suggested")
        by_strategy.setdefault(s, Counter())["delivered"] += 1
        by_strategy[s][st] += 1
        if st == "rejected":
            for code in status[d["suggestion_id"]].get("reason_codes") or []:
                reasons.setdefault(s, Counter())[code] += 1
    table = {}
    for s, c in sorted(by_strategy.items()):
        delivered = c["delivered"]
        engaged = c["accepted"] + c["assigned"] + c["published"]
        table[s] = {"delivered": delivered, "accepted": c["accepted"], "assigned": c["assigned"],
                    "published": c["published"], "rejected": c["rejected"], "expired": c["expired"],
                    "awaiting": c["suggested"],
                    "acceptance_rate": round(engaged / delivered, 3) if delivered else None,
                    "rejection_reasons": dict(sorted(reasons.get(s, Counter()).items()))}
    return {"strategies": table, "deliveries": len(deliveries),
            "note": "counts from the ledger; acceptance = accepted + assigned + published over delivered"}
