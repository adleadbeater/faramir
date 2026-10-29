"""Fact check for a written title, against the story's fact sheet (titles.brief.Brief).

Strong wording is allowed when the evidence backs it — "No. 1" with a chart rank of 1, "Top 3" inside
a top-3 streak, a rival named when the fact sheet names it — and refused otherwise. Every dollar
figure must be one of the verified figures at the precision written. Style findings from the Title
Crafting Rules (avoid list) are warnings, not failures.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from faramir.titles import data
from faramir.validate import ANTICIPATED_RE, COMPLETED_RE, parse_money

# Rob's call (2026-10-28): 95 like Aragorn and Strider — 10 characters of trim headroom for editors over
# the Title Crafting Rules' 85-character site cap.
MAX_CHARS = int(os.environ.get("FARAMIR_TITLE_MAX_CHARS", "95"))
FIRST_RE = re.compile(r"(?<![\w-])(first|only|ever|never)(?![\w])", re.I)
OPENING_RE = re.compile(r"(?<![\w-])(opening weekend|opening|debuts?|opens)(?![\w])", re.I)
RANK_RE = re.compile(r"(?<![\w-])(?:no\.\s?(\d+)|#(\d+)|number one|top[- ](\d+))(?![\w])", re.I)
RECORD_RE = re.compile(r"(?<![\w-])(record|biggest|largest|highest[- ]grossing|all[- ]time|career[- ]best|best[- ]ever)(?![\w])", re.I)
SOFT_RE = re.compile(r"(?<![\w-])(almost|nearly|could)(?![\w])", re.I)


@dataclass
class Check:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _figure_ok(val: float, tol: float, figures: list[float]) -> bool:
    """A written figure matches a verified one at the precision written ($24M covers $23.5M–$24.5M)."""
    return any(abs(f - val) <= tol + 1 for f in figures)


def check(title: str, brief, other_titles: list[str] | None = None) -> Check:
    r, w = [], []
    t = title.strip()
    if len(t) > MAX_CHARS:
        r.append(f"too_long: {len(t)} characters (max {MAX_CHARS})")
    if re.search(r"[–—]", t):
        r.append("dash: em/en dash")
    if t.endswith("?"):
        r.append("question: interrogative title")
    for val, tol, text in parse_money(t):
        if not _figure_ok(val, tol, brief.figures):
            r.append(f"unverified_figure: {text}")
    completed, anticipated = bool(COMPLETED_RE.search(t)), bool(ANTICIPATED_RE.search(t))
    if brief.status == "anticipated" and completed and not anticipated:
        r.append("tense: completed wording for an event that hasn't happened")
    if brief.status == "confirmed" and anticipated and not completed and brief.strategy != "chase":
        w.append("tense: forward wording for an event that already happened")
    if re.search(r"(?<![\w-])officially(?![\w])", t, re.I) and brief.status != "confirmed":
        r.append("officially: used on an event that hasn't happened")
    if SOFT_RE.search(t):
        r.append("anti_pattern: almost/nearly/could")
    ev = brief.rank_evidence or {}
    for m in RANK_RE.finditer(t):
        n = int(m.group(1) or m.group(2) or 1) if (m.group(1) or m.group(2) or "number one" in m.group(0).lower()) else None
        top = int(m.group(3)) if m.group(3) else None
        if n is not None and ev.get("chart_rank") != n:
            r.append(f"rank_unsupported: '{m.group(0)}' (chart rank {ev.get('chart_rank')})")
        if top is not None and not ((ev.get("tier") and top >= ev["tier"]) or (ev.get("chart_rank") and ev["chart_rank"] <= top)):
            r.append(f"rank_unsupported: '{m.group(0)}'")
    if RECORD_RE.search(t) and brief.strategy != "star_record":
        r.append(f"record_unsupported: '{RECORD_RE.search(t).group(0)}'")
    if FIRST_RE.search(t):
        r.append(f"claim_unsupported: '{FIRST_RE.search(t).group(0)}' (firsts and onlys aren't on the fact sheet)")
    if OPENING_RE.search(t) and not getattr(brief, "opening", False):
        r.append("opening_unsupported: the fact sheet doesn't say this is the film's opening")
    allowed = {n.lower() for n in brief.names}
    for other in other_titles or []:
        if len(other) >= 4 and other.lower() not in allowed and re.search(
                r"(?<!\w)" + re.escape(other) + r"(?!\w)", t, re.I):
            r.append(f"film_not_in_story: '{other}'")
    genres = {g.lower() for g in (getattr(brief, "genres", None) or [])}
    if {"action", "thriller"} <= genres and re.search(r"(?<![\w-])thriller", t, re.I) \
            and not re.search(r"(?<![\w-])action thriller", t, re.I):
        w.append("genre: an Action + Thriller film should be called an 'Action Thriller'")
    for phrase in data.AVOID:
        if re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w])", t, re.I):
            w.append(f"avoid_list: '{phrase}'")
    return Check(not r, r, w)
