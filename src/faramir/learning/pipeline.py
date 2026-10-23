"""Outcome rows for the estimates: delivered picks → published URL → age-comparable outcome.

Joins ``deliveries`` (strategy), the latest ``editorial`` status (published URL, headline) and
``outcome_observations`` (cumulative sessions at known ages). Only outcomes measured at a
comparable age (14–21 days) become rows; everything else is counted as pending.
"""

from __future__ import annotations

from datetime import date, timedelta

from faramir.headlines.taxonomy import primary
from faramir.learning.outcomes import at_age
from faramir.reports.editorial_report import latest_status


def outcome_rows(ledger, cfg: dict) -> tuple[list[dict], dict]:
    status = latest_status(ledger.read("editorial"))
    obs = ledger.read("outcome_observations")
    rows, pending = [], {"no_published_url": 0, "no_observation": 0, "no_age_comparable_observation": 0}
    lo, hi = cfg["outcome_age_window_days"]
    for d in ledger.read("deliveries"):
        st = status.get(d.get("suggestion_id") or "")
        url = (st or {}).get("published_url")
        if not url:
            pending["no_published_url"] += 1
            continue
        o = at_age(obs, url, cfg["outcome_age_days"], (lo, hi))
        if o.status != "ok":
            pending[o.status] += 1
            continue
        rows.append({"strategy": d["strategy"], "construction": primary((st or {}).get("headline") or ""),
                     "sessions": o.sessions, "age_days": o.age_days, "failure": o.failure,
                     "heavy_hitter": o.heavy_hitter, "url": url})
    return rows, pending


def explored_recently(ledger, today: str, days: int = 7) -> int:
    start = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    return sum(1 for d in ledger.read("decisions")
               if (d.get("exploration") or {}).get("flag") and (d.get("disposition") or {}).get("value") == "suggested"
               and (d.get("trigger") or {}).get("period", "") >= start)


def deliveries_by_strategy(ledger) -> dict:
    out: dict[str, int] = {}
    for d in ledger.read("deliveries"):
        out[d["strategy"]] = out.get(d["strategy"], 0) + 1
    return out
