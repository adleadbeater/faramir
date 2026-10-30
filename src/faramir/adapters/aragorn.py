"""Aragorn integration behind a replaceable interface (plan §8).

``TitleRefiner.refine(headline, alt, evidence) -> {headline, alt, changes, refiner_version}``.
``NullRefiner`` (the default) changes nothing: Aragorn has no callable API today (option D).
Any refiner's output is re-validated by Faramir — facts, meaning and the 85-character cap — and
discarded if it fails, so a refiner can never introduce an unverified claim.
"""

from __future__ import annotations

from typing import Protocol

from faramir.validate import HEADLINE_MAX, meaning_changes, validate_pick


class TitleRefiner(Protocol):
    version: str

    def refine(self, headline: str, alt: str, evidence: dict) -> dict:
        ...


class NullRefiner:
    version = "null-1"

    def refine(self, headline: str, alt: str, evidence: dict) -> dict:
        return {"headline": headline, "alt": alt, "changes": [], "refiner_version": self.version}


def refine_and_validate(pick: dict, refiner: TitleRefiner, lexicon: list[str]) -> tuple[dict, dict]:
    """(pick to use, aragorn record). The refined headline is used only if it passes every check."""
    before = pick.get("headline", "")
    try:
        out = refiner.refine(before, pick.get("angle", ""), pick.get("evidence") or {})
    except Exception as exc:  # a failing refiner never blocks the pick
        return pick, {"used": False, "before": before, "after": None, "refiner_version": getattr(refiner, "version", "?"),
                      "reason": f"refiner error: {type(exc).__name__}"}
    after = (out or {}).get("headline") or before
    record = {"used": False, "before": before, "after": after, "refiner_version": out.get("refiner_version"),
              "changes": out.get("changes", [])}
    if after == before:
        record["reason"] = "unchanged"
        return pick, record
    candidate = dict(pick, headline=after)
    v = validate_pick(candidate, lexicon)
    problems = list(v.reasons) + meaning_changes(pick, after)
    if len(after) > HEADLINE_MAX:
        problems.append(f"over {HEADLINE_MAX} characters")
    if problems:
        record["reason"] = "rejected: " + "; ".join(problems[:3])
        return pick, record
    record["used"] = True
    return candidate, record
