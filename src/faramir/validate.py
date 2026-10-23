"""Validate a bound pick's words against its evidence (Phase 0 rules).

Input: a pick accepted by faramir.binding (it carries ``evidence``) plus the
model's declared ``claims``. Every check is independent of the others and
returns a reason; a pick with any reason is dropped (or, if the only reason is
length, given one regeneration attempt by the caller).

1. Declared claims: each must be supported by the bound candidate. Rank,
   record, superlative, chart-position, first/only and count claims are never
   supported in Phase 0. At least one event claim matching the candidate is
   required.
2. Lexical scan of headline + angle (with the active and comparison titles
   masked out), independent of the declared claims:
   * rank/record/superlative language;
   * completed wording on an anticipated event, or vice versa;
   * a genre descriptor that neither film has;
   * another film's title (from the corpus / today's payload).
3. Every dollar figure must match a reported evidence value — active film's
   current gross, comparison's reported gross, threshold, or the gap between
   them — within the precision the text implies. A missing gross is never a
   valid source.
4. Headline length <= 85 characters (counted exactly; never truncated).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADLINE_MAX = 85

MONEY_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(billion|million|thousand|bn|b|m|k)?(?![\w])", re.I)
UNIT = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3, "": 1.0}

SUPERLATIVE_RE = re.compile(
    r"(?<![\w-])(no\.\s?\d+|#\s?\d+|number one|\d+(?:st|nd|rd|th)|biggest|largest|highest(?:-grossing)?|"
    r"top[- ]\d+|top-grossing|records?|record-breaking|all-time|ever|best|most|first|only|leading|dethron\w*)(?![\w])",
    re.I)
COMPLETED_RE = re.compile(
    # Inflected forms only: "is about to pass" must not count as completed wording.
    r"(?<![\w-])(pass(?:es|ed)|surpass(?:es|ed)|tops|topped|beat|beats|beaten|overtakes|overtook|overtaken|"
    r"cross(?:es|ed)|outgross(?:es|ed)|eclips(?:es|ed)|outdid|outdoes|clears|cleared|hits|zooms past|blows past)(?![\w])",
    re.I)
ANTICIPATED_RE = re.compile(
    r"(?<![\w-])(nears?|nearing|closing in|close to|about to|on track|poised|set to|could|approach(?:es|ing)?|"
    r"within \$|edging (?:toward|closer)|inches (?:toward|closer)|chasing|eyes|eyeing|on the verge)(?![\w])",
    re.I)
GENRE_WORDS = {
    "horror": "horror", "comedy": "comedy", "animated": "animation", "animation": "animation",
    "sci-fi": "science fiction", "science fiction": "science fiction", "thriller": "thriller",
    "action": "action", "fantasy": "fantasy", "romance": "romance", "rom-com": "romance",
    "romantic comedy": "romance", "documentary": "documentary", "musical": "music", "western": "western",
    "war": "war", "crime": "crime", "mystery": "mystery", "family": "family", "adventure": "adventure",
}
GENRE_RE = re.compile(
    r"(?<![\w-])(" + "|".join(sorted((re.escape(g) for g in GENRE_WORDS), key=len, reverse=True)) +
    r")\s+(?:movie|movies|film|films|flick|hit|hits|sequel|franchise|title|blockbuster|release)s?(?![\w])", re.I)
RELATION_CATEGORY = {
    "franchise": {"franchise_sibling"},
    "same_director": {"creative_head_to_head_director"},
    "same_star": {"creative_head_to_head_cast"},
}
NEVER_SUPPORTED = {"rank", "record", "superlative", "chart_position", "first_or_only", "count"}


@dataclass
class Validation:
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons

    @property
    def only_length(self) -> bool:
        return bool(self.reasons) and all(r.startswith("headline_too_long") for r in self.reasons)


def _reported(entity: dict | None, axis: str):
    if not entity:
        return None
    if entity.get(f"{axis}_status", "reported") != "reported":
        return None
    v = entity.get(f"{axis}_lifetime")
    return v if isinstance(v, (int, float)) and v > 0 else None


def _active(ev: dict, axis: str):
    v = (ev.get("active") or {}).get(f"{axis}_today")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def parse_money(text: str) -> list[tuple[float, float, str]]:
    """[(value, tolerance, matched text)] for every dollar figure in text."""
    out = []
    for m in MONEY_RE.finditer(text or ""):
        num, unit = m.group(1), (m.group(2) or "").lower()
        mult = UNIT[unit]
        if "," in num:
            value, decimals = float(num.replace(",", "")), 0
        else:
            value = float(num)
            decimals = len(num.split(".")[1]) if "." in num else 0
        out.append((value * mult, 0.5 * (10 ** -decimals) * mult, m.group(0)))
    return out


def evidence_values(ev: dict) -> list[float]:
    event = ev.get("event") or {}
    axis = event.get("axis")
    vals = []
    for a in ("domestic", "worldwide"):
        for v in (_active(ev, a), _reported(ev.get("comp"), a)):
            if v:
                vals.append(float(v))
    target = _reported(ev.get("comp"), axis) if ev.get("comp") else event.get("threshold")
    current = _active(ev, axis)
    if event.get("threshold"):
        vals.append(float(event["threshold"]))
    if target and current:
        vals.append(abs(float(target) - float(current)))  # "within $3M of …" / "by $2M"
    return vals


def _mask(text: str, titles: list[str]) -> str:
    for t in sorted((t for t in titles if t), key=len, reverse=True):
        text = re.sub(re.escape(t), " ", text, flags=re.I)
    return text


def _completed_event(ev: dict) -> bool:
    event = ev.get("event") or {}
    return event.get("kind") == "just_passed" or (event.get("kind") == "milestone" and event.get("milestone_type") == "just_passed")


def check_claims(pick: dict) -> list[str]:
    ev = pick["evidence"]
    event, comp = ev.get("event") or {}, ev.get("comp")
    kind, axis, mtype = event.get("kind"), event.get("axis"), event.get("milestone_type")
    cats = set(ev.get("categories") or [])
    genres = {g.lower() for g in ((ev.get("active") or {}).get("genres") or []) + ((comp or {}).get("genres") or [])}
    reasons, event_claims = [], 0
    for c in pick.get("claims") or []:
        t = c.get("type")
        if t in NEVER_SUPPORTED:
            reasons.append(f"claim_unsupported:{t}: {c.get('text')!r} — no evidence in Phase 0 supports {t} claims")
            continue
        if t in ("passed_comp", "nearing_comp"):
            want_kind = "just_passed" if t == "passed_comp" else "about_to_pass"
            comp_value = _reported(comp, axis)
            if comp is None or c.get("comp_tmdb_id") != comp.get("tmdb_id") or c.get("axis") != axis or kind != want_kind:
                reasons.append(f"claim_mismatch:{t}: does not match the bound {kind} {axis} event")
            elif comp_value is None:
                reasons.append(f"claim_unavailable_metric:{t}: comparison {axis} gross is not reported")
            else:
                current = _active(ev, axis)
                if current is None or (t == "passed_comp" and current < comp_value) or (t == "nearing_comp" and current >= comp_value):
                    reasons.append(f"claim_contradicted:{t}: current {axis} gross {current} vs comparison {comp_value}")
                else:
                    event_claims += 1
        elif t in ("crossed_milestone", "nearing_milestone"):
            want = "just_passed" if t == "crossed_milestone" else "about_to_pass"
            if kind != "milestone" or mtype != want or c.get("threshold") != event.get("threshold") or c.get("axis") != axis:
                reasons.append(f"claim_mismatch:{t}: does not match the bound milestone ({mtype} {event.get('threshold')} {axis})")
            else:
                event_claims += 1
        elif t == "relation":
            need = RELATION_CATEGORY.get(c.get("relation") or "", set())
            if not (need & cats) or (comp or {}).get("identity_status") != "verified":
                reasons.append(f"claim_unsupported:relation:{c.get('relation')}: candidate categories {sorted(cats)}")
        elif t == "genre_descriptor":
            g = GENRE_WORDS.get(str(c.get("genre") or "").lower(), str(c.get("genre") or "").lower())
            if g not in genres:
                reasons.append(f"claim_unsupported:genre:{c.get('genre')}: films are {sorted(genres)}")
    if event_claims == 0:
        reasons.append("claims_missing_event: no declared claim states the bound event")
    return reasons


def lexical_checks(pick: dict, other_titles: list[str]) -> list[str]:
    ev = pick["evidence"]
    comp = ev.get("comp") or {}
    own = [(ev.get("active") or {}).get("title") or "", pick.get("active_title") or "",
           comp.get("title") or "", pick.get("comp_title") or ""]
    raw = f"{pick.get('headline', '')}\n{pick.get('angle', '')}"
    text = _mask(raw, own)
    reasons = []
    for m in SUPERLATIVE_RE.finditer(text):
        word = m.group(1)
        if word.lower() == "best" and re.match(r"best\s+picture", text[m.start():], re.I) and comp.get("is_best_picture"):
            continue
        reasons.append(f"lexical_superlative: {word!r}")
        break
    completed, anticipated = bool(COMPLETED_RE.search(text)), bool(ANTICIPATED_RE.search(text))
    if _completed_event(ev):
        if anticipated and not completed:
            reasons.append("tense_mismatch: anticipatory wording for an event that already happened")
    elif completed and not anticipated:
        reasons.append("tense_mismatch: completed wording for an event that has not happened yet")
    genres = {g.lower() for g in ((ev.get("active") or {}).get("genres") or []) + (comp.get("genres") or [])}
    for m in GENRE_RE.finditer(text):
        if GENRE_WORDS[m.group(1).lower()] not in genres:
            reasons.append(f"genre_unsupported: {m.group(0)!r} (films are {sorted(genres)})")
    own_lower = [o.lower() for o in own if o]
    for t in other_titles:
        if not t or any(t.lower() in o for o in own_lower):
            continue
        if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", text):
            reasons.append(f"third_film_named: {t!r}")
            break
    return reasons


def figure_checks(pick: dict) -> list[str]:
    ev = pick["evidence"]
    comp = ev.get("comp")
    reasons = []
    if comp:
        dom, ww = _reported(comp, "domestic"), _reported(comp, "worldwide")
        if dom and ww and ww < dom:
            reasons.append("comp_partial_run: comparison worldwide < domestic")
        axis = (ev.get("event") or {}).get("axis")
        if _reported(comp, axis) is None:
            reasons.append(f"comp_metric_unavailable: comparison {axis} gross is not reported")
    known = evidence_values(ev)
    for value, tol, txt in parse_money(f"{pick.get('headline', '')} {pick.get('angle', '')}"):
        if not any(abs(value - k) <= tol + 1e-6 for k in known):
            reasons.append(f"unverified_figure: {txt.strip()} matches no reported evidence value")
    return reasons


def length_check(pick: dict) -> list[str]:
    n = len(pick.get("headline") or "")
    return [f"headline_too_long: {n} > {HEADLINE_MAX} characters"] if n > HEADLINE_MAX else []


def validate_pick(pick: dict, other_titles: list[str]) -> Validation:
    v = Validation()
    v.reasons += check_claims(pick)
    v.reasons += lexical_checks(pick, other_titles)
    v.reasons += figure_checks(pick)
    v.reasons += length_check(pick)
    return v


def title_lexicon(corpus_films: list[dict], active_films: list[dict]) -> list[str]:
    """Titles distinctive enough to detect in prose: 2+ words, or one word of 7+ letters."""
    titles = {str(f.get("title") or "") for f in corpus_films if f.get("identity_status", "verified") == "verified"}
    titles |= {str(f.get("title") or "") for f in active_films}
    return sorted(t for t in titles if t and (len(t.split()) >= 2 or len(t) >= 7))
