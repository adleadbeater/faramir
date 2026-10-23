"""Strategy Performance & Prioritization report (plan §6), from the ledger and the estimates."""

from __future__ import annotations

from faramir.learning.estimates import estimate
from faramir.learning.pipeline import outcome_rows
from faramir.reports import editorial_report, explainer


def build(ledger, cfg: dict) -> dict:
    rows, pending = outcome_rows(ledger, cfg)
    by_strategy = estimate(rows, "strategy", k=cfg["shrinkage_k"], min_n=cfg["experimental_below_n_eff"])
    by_construction = estimate(rows, "construction", k=cfg["shrinkage_k"], min_n=cfg["experimental_below_n_eff"])
    f = explainer.funnel(ledger, {r["url"] for r in rows})
    ed = editorial_report.build(ledger)
    proposals = [p for p in ledger.read("priority_config")]
    calibrated = by_strategy["__global__"].status != "calibration_pending"
    return {
        "calibration": "estimated" if calibrated else "pending — no age-comparable outcomes yet "
                                                       f"(pending: {pending})",
        "supply_and_funnel": f["funnel"],
        "acceptance": {s: {k: v[k] for k in ("delivered", "acceptance_rate", "rejection_reasons")}
                       for s, v in ed["strategies"].items()},
        "outcomes_14d_by_strategy": {k: v.as_dict() for k, v in by_strategy.items()},
        "outcomes_14d_by_construction": {k: v.as_dict() for k, v in by_construction.items()},
        "exploration_share": f["exploration_suggested"],
        "priority_changes": [{"to": p["to_version"], "diff": p["diff"], "reasons": p["reasons"], "applied": p["applied"]}
                             for p in proposals],
        "config_version": cfg["version"],
    }
