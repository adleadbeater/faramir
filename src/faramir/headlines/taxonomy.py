"""Headline construction taxonomy for box-office stories, and a deterministic tagger.

A construction is *how* a headline is built, independent of the strategy that found the story
(plan §5: strategy and headline are separate factors). Multi-label: a headline can lead with a
number *and* be a comparison. ``primary()`` picks one by a fixed precedence for reporting.
"""

from __future__ import annotations

import re

# (construction, pattern, description) — order is the primary-label precedence.
CONSTRUCTIONS = [
    ("chart_position", r"\b(no\.\s?1|#1|number one|box office crown|tops? the (weekend )?box office|wins? the weekend|"
                       r"(?:stays?|remains?) (?:at )?(?:no\.|number) ?\d)\b", "rank on the chart"),
    ("record_claim", r"\b(record|biggest|highest[- ]grossing|best[- ]ever|all[- ]time|career[- ]best|franchise[- ]best)\b",
     "a record or superlative"),
    ("contrast_upset", r"\b(quietly|stuns?|upsets?|surprise|shock(ing|s)?|giant[- ]killer|outdoes|out-?grosses|"
                       r"against all odds|sleeper)\b", "an underdog contrast"),
    ("passes_comparison", r"\b(pass(es|ed)?|surpass(es|ed)?|tops|topped|beats?|overtakes?|overtook|eclips(es|ed)|"
                          r"outpaces?|moves past|blows past|zooms past|leaves .{1,40} behind)\b(?!\s+(the\s+)?\$)",
     "X passes Y (a film, not a round number)"),
    ("anticipation", r"\b(nears?|nearing|closing in|closes in|on track|poised|set to|about to|approach(es|ing)?|"
                     r"inches? (closer|toward)|within (striking )?distance|eyes)\b", "about to happen"),
    ("milestone_crossing", r"\b(cross(es|ed)|hits?|reach(es|ed)|surges? past|tops|pass(es|ed)?|surpass(es|ed)?) "
                           r"\$[\d.,]+\s?(m|million|b|billion)\b|"
                           r"\$[\d.,]+\s?(m|million|b|billion) milestone", "crosses a round number"),
    ("opening_weekend", r"\b(opening weekend|debuts?|opens? (to|with)|premiere weekend|launch(es)? (to|with))\b",
     "the opening"),
    ("ongoing_run", r"\b(keeps? (getting|climbing|growing)|continues?|still (going|climbing)|another|stays? strong|legs)\b",
     "the run continues"),
    ("question", r"\?\s*$", "a question"),
]
LEADS_WITH_NUMBER = re.compile(r"^\s*\$[\d.,]+\s?(m|million|b|billion)?\b", re.I)
_COMPILED = [(name, re.compile(pat, re.I), desc) for name, pat, desc in CONSTRUCTIONS]
NAMES = [c[0] for c in CONSTRUCTIONS] + ["lead_with_number", "other"]


def tag(headline: str) -> list[str]:
    tags = [name for name, rx, _ in _COMPILED if rx.search(headline or "")]
    if LEADS_WITH_NUMBER.search(headline or ""):
        tags.insert(0, "lead_with_number")
    return tags or ["other"]


def primary(headline: str) -> str:
    tags = tag(headline)
    for name in [c[0] for c in CONSTRUCTIONS]:
        if name in tags:
            return name
    return tags[0]


# Which constructions honestly fit each strategy's evidence (a Chase can't "pass" yet; a Countdown
# hasn't crossed). Used to keep recommendations truthful before any performance ranking.
FITS = {
    "overtake": ["passes_comparison", "milestone_crossing", "lead_with_number", "contrast_upset", "ongoing_run"],
    "chase": ["anticipation", "lead_with_number", "ongoing_run"],
    "countdown": ["anticipation", "lead_with_number", "ongoing_run"],
    "takeover": ["chart_position", "contrast_upset", "opening_weekend", "lead_with_number"],
    "hold": ["chart_position", "ongoing_run", "lead_with_number"],
    "streak": ["chart_position", "ongoing_run"],
    "legs": ["ongoing_run", "lead_with_number"],
    "exit": ["ongoing_run"],
    "dark_horse": ["contrast_upset", "passes_comparison", "lead_with_number"],
    "star_record": ["passes_comparison", "lead_with_number"],
    "global_split": ["lead_with_number"],
    "studio_battle": ["chart_position", "lead_with_number"],
    "premium_run": ["lead_with_number"],
}
