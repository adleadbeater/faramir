"""Global Split (strategy 11): an unusual domestic/international split.

Fires when F's international share deviates more than 25% [CAL] from its genre cohort's median
(cohort ≥ 15 corpus films with both grosses reported [CAL]) at least 14 days after its
international release. An international share above 80% adds a revision-risk flag.

**Disabled in the registry:** no source gives the international release date or rollout status,
so "≥ 14 days since international release" can't be verified. Without ``rollout_complete`` the
event is ``unverifiable`` (never delivered). Worldwide figures come from BOM (licensing open).
"""

from __future__ import annotations

import statistics

from faramir.corpus import reported
from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, axis_source, corpus_source, film_ref

DEVIATION = 0.25
MIN_COHORT = 15
REVISION_RISK_SHARE = 0.80


def detect(film: dict, corpus: list[dict], data_date: str, gross_as_of: str | None,
           rollout_complete: bool | None = None) -> DetectorResult:
    out = DetectorResult()
    dom, ww = float(film.get("domestic_today") or 0), float(film.get("worldwide_today") or 0)
    target = {"type": "split", "value": "international_share"}
    if not dom or not ww or ww < dom:
        return out
    share = (ww - dom) / ww
    genres = set(film.get("genres") or [])
    cohort = [(reported(f, "worldwide") - reported(f, "domestic")) / reported(f, "worldwide") for f in corpus
              if genres & set(f.get("genres") or []) and reported(f, "worldwide") and reported(f, "domestic")
              and reported(f, "worldwide") >= reported(f, "domestic")]
    if len(cohort) < MIN_COHORT:
        out.reject("global_split", film, "worldwide", target, "cohort_too_small", f"{len(cohort)} films")
        return out
    median = statistics.median(cohort)
    deviation = (share - median) / median if median else 0
    if abs(deviation) <= DEVIATION:
        out.reject("global_split", film, "worldwide", target, "normal_for_genre", f"share {share:.0%} vs median {median:.0%}")
        return out
    status = "confirmed" if rollout_complete else "unverifiable"
    out.emit(Event(
        strategy="global_split", film=film_ref(film), period=data_date, axis="worldwide", target=target, status=status,
        evidence={"domestic": dom, "worldwide": ww, "international_share": round(share, 3), "cohort_median": round(median, 3),
                  "deviation": round(deviation, 3), "cohort_n": len(cohort), "rollout_complete": rollout_complete,
                  "revision_risk": share > REVISION_RISK_SHARE},
        sources=[axis_source(film, "domestic", data_date), axis_source(film, "worldwide", data_date), corpus_source(gross_as_of)],
        reason=(f"{film_ref(film)['title']} earns {share:.0%} internationally vs a {median:.0%} median for its genres "
                f"({deviation:+.0%}, n = {len(cohort)})" + ("" if rollout_complete else
                "; international rollout status unknown, so unverifiable")),
        reason_codes=["split_deviation"] + (["revision_risk"] if share > REVISION_RISK_SHARE else [])
        + ([] if rollout_complete else ["rollout_unknown"]), strength=0.55))
    return out
