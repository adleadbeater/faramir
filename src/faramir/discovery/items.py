"""Discovery items and claim verification.

A Discovery item is a *proposed* pattern, never an established fact. Every number it states is
a structured claim ``{film_key, metric, value, period, source_row}`` that must match stored,
committed weekend data exactly (within the stated precision). An item with any unverifiable
claim is dropped and logged — never shown (plan §4).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from faramir.strategies.weekend import Weekend

METRICS = ("weekend_gross", "theaters", "theater_avg", "total_gross", "source_rank", "days_in_release",
           "weekend_change_pct", "theater_change_pct", "rerelease_share_pct")


@dataclass
class Claim:
    film_key: str | None          # None for chart-level claims
    metric: str
    value: float
    period: str                   # weekend Friday
    source_row: str               # collection_id (+ film key) the value comes from
    tolerance: float = 0.0


@dataclass
class DiscoveryItem:
    generator: str
    period: str
    film_key: str | None
    title: str | None
    metric: str
    value: float
    baseline: dict                # what it is anomalous against (median, z, cohort n …)
    claims: list[Claim]
    why_it_matters: str
    headline: str
    alternative: str
    pattern_tags: list[str]
    status: str = "proposed"      # proposed | dropped_unverified
    similar_prior: list[dict] = field(default_factory=list)
    novelty: float = 1.0
    verification: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"discovery|{self.generator}|{self.film_key}|{self.period}|{self.metric}".encode()).hexdigest()[:20]

    def to_record(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["label"] = "proposed pattern — needs editorial review; not an established fact"
        return d


def _row_value(row, metric: str):
    return {"weekend_gross": row.gross, "theaters": row.theaters, "total_gross": row.total_gross,
            "source_rank": row.source_rank, "days_in_release": row.days_in_release,
            "theater_avg": (row.gross / row.theaters) if row.gross is not None and row.theaters else None}.get(metric)


def verify_claim(claim: Claim, weekends: dict[str, Weekend]) -> str | None:
    """None if the claim matches stored data; otherwise why not."""
    w = weekends.get(claim.period)
    if w is None:
        return f"no committed weekend {claim.period}"
    if claim.metric not in METRICS:
        return f"unknown metric {claim.metric!r}"
    if claim.film_key is None:
        if claim.metric != "rerelease_share_pct":
            return "chart-level claims support rerelease_share_pct only"
        if not claim.source_row.startswith(w.collection_id):
            return "source row is not this weekend's collection"
        total = sum(r.gross or 0 for r in w.rows)
        share = 100 * sum(r.gross or 0 for r in w.rows if r.engagement == "rerelease") / total if total else 0
        return None if abs(share - claim.value) <= max(claim.tolerance, 1e-6) else f"re-release share is {share:.2f}%"
    row = w.by_key().get(claim.film_key)
    if row is None:
        return f"{claim.film_key} is not on the {claim.period} chart"
    if not claim.source_row.startswith(w.collection_id):
        return "source row is not this weekend's collection"
    if claim.metric in ("weekend_change_pct", "theater_change_pct"):
        return "percentage changes are derived; claim the two underlying rows instead"
    actual = _row_value(row, claim.metric)
    if actual is None:
        return f"{claim.metric} is unavailable for {claim.film_key}"
    if abs(float(actual) - float(claim.value)) > max(claim.tolerance, 1e-6):
        return f"{claim.metric} is {actual}, claim says {claim.value}"
    return None


def verify(item: DiscoveryItem, weekends: dict[str, Weekend]) -> DiscoveryItem:
    problems = [p for p in (verify_claim(c, weekends) for c in item.claims) if p]
    if not item.claims:
        problems.append("an item with no claims cannot be verified")
    item.verification = problems
    item.status = "proposed" if not problems else "dropped_unverified"
    return item
