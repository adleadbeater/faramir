"""Discovery's open pass (plan §4): a model proposes patterns from the week's chart; every
proposal must come back as structured claims and is checked against stored data. Unverifiable
claims drop the proposal (logged, never shown).

``NullOpenPass`` (the default) proposes nothing and costs nothing. A Claude-backed proposer is
an interface away; it is not wired in, because running it means new paid API calls that are not
authorized.
"""

from __future__ import annotations

from typing import Protocol

from faramir.discovery.items import Claim, DiscoveryItem, verify


class OpenPass(Protocol):
    def propose(self, weekend_summary: dict) -> list[dict]:
        """[{pattern, metric, film_key, title, value, claims: [{film_key, metric, value, period, source_row}],
        why_it_matters, headline, alternative, tags}]"""


class NullOpenPass:
    name = "null"

    def propose(self, weekend_summary: dict) -> list[dict]:
        return []


def run(proposer: OpenPass, weekend_summary: dict, weekends: dict) -> tuple[list[DiscoveryItem], list[dict]]:
    kept, dropped = [], []
    for p in proposer.propose(weekend_summary) or []:
        try:
            claims = [Claim(c.get("film_key"), c["metric"], float(c["value"]), c["period"], c["source_row"],
                            float(c.get("tolerance", 0))) for c in p.get("claims") or []]
        except (KeyError, TypeError, ValueError) as exc:
            dropped.append({"pattern": p.get("pattern"), "reason": f"malformed claims: {exc}"})
            continue
        item = verify(DiscoveryItem(
            "open_pass", weekend_summary["weekend_start"], p.get("film_key"), p.get("title"), p.get("metric", ""),
            float(p.get("value") or 0), {"proposed_by": getattr(proposer, "name", "model")}, claims,
            p.get("why_it_matters", ""), p.get("headline", ""), p.get("alternative", ""),
            list(p.get("tags") or [])), weekends)
        if item.status == "proposed":
            kept.append(item)
        else:
            dropped.append({"pattern": p.get("pattern"), "reason": "; ".join(item.verification)})
    return kept, dropped
