"""Evidence-based headline recommendation (plan §6 "headline_evidence", §2 headlines).

For an event: the constructions that honestly fit its strategy (taxonomy ``FITS``), ranked by
MovieWeb's historical box-office results for that construction (cumulative sessions per
article vs the box-office baseline, with n; small samples are experimental and rank below
measured ones). Proven MovieWeb Power Words that fit the event are suggested too. Every claim
in the output names its evidence; nothing is ranked on data that doesn't exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faramir.analytics.power_words import PowerWord
from faramir.headlines.analysis import Analysis
from faramir.headlines.taxonomy import FITS

# Power Words that are factually safe for box-office events (no superlatives, no rank claims
# unless the event carries rank evidence). Anything else is never suggested.
EVENT_SAFE_WORDS = {"officially", "box office", "milestone", "passes", "crosses", "climbs", "surges", "now"}


@dataclass
class Recommendation:
    strategy: str
    constructions: list[dict] = field(default_factory=list)   # ranked: {construction, evidence, rank_basis}
    power_words: list[dict] = field(default_factory=list)
    rationale_codes: list[str] = field(default_factory=list)
    calibration: str = "historical_prior"                     # historical_prior | no_evidence

    def guidance(self) -> dict:
        """The compact block passed to Claude with the candidate."""
        return {"preferred_constructions": [c["construction"] for c in self.constructions[:2]],
                "power_words": [w["phrase"] for w in self.power_words[:3]],
                "evidence": self.rationale_codes[:4]}


def recommend(strategy: str, analysis: Analysis | None, words: list[PowerWord] | None = None) -> Recommendation:
    rec = Recommendation(strategy)
    fits = FITS.get(strategy, [])
    if analysis is None:
        rec.calibration = "no_evidence"
        rec.constructions = [{"construction": c, "evidence": None, "rank_basis": "fit_only"} for c in fits]
        rec.rationale_codes = ["no_cerebro_snapshot: constructions ordered by fit only"]
        return rec
    scored = []
    for c in fits:
        s = analysis.stat(c)
        if s is None:
            scored.append((2, 0.0, {"construction": c, "evidence": None, "rank_basis": "no_history"}))
            continue
        tier = 1 if s.experimental else 0
        scored.append((tier, -(s.lift_vs_baseline or 0.0), {"construction": c, "evidence": s.as_dict(),
                                                            "rank_basis": "experimental" if s.experimental else "measured"}))
    scored.sort(key=lambda t: (t[0], t[1]))
    rec.constructions = [x[2] for x in scored]
    for c in rec.constructions:
        ev = c["evidence"]
        if ev:
            lift = ev["lift_vs_baseline"]
            rec.rationale_codes.append(
                f"construction:{c['construction']}:{'+' if (lift or 0) >= 0 else ''}{(lift or 0):.0%} median vs box-office "
                f"baseline (n={ev['n']}{', experimental' if ev['experimental'] else ''}, cumulative sessions/article, "
                f"as of {analysis.as_of})")
        else:
            rec.rationale_codes.append(f"construction:{c['construction']}:no MovieWeb history")
    for w in words or []:
        if w.proven and w.phrase.lower() in EVENT_SAFE_WORDS:
            rec.power_words.append({"phrase": w.phrase, "mw_uses": w.uses, "mw_sessions_per_article": w.spa,
                                    "mw_failure_rate_pct": w.failure_rate})
    rec.power_words.sort(key=lambda w: -(w["mw_sessions_per_article"] or 0))
    for w in rec.power_words[:3]:
        rec.rationale_codes.append(f"power_word:{w['phrase']}:MW {w['mw_sessions_per_article']:.0f} sessions/article "
                                   f"(n={w['mw_uses']})")
    return rec
