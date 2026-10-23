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
   * rank/record/superlative language — context-aware: "in its first weekend",
     "only $3M away", "ever since", "most of its gross" are ordinary language;
   * completed wording on an anticipated event, or vice versa;
   * the comparison named as the film doing the passing (direction reversed);
   * a genre descriptor attributed to a film that does not have that genre;
   * another film's title (from the corpus / today's payload).
3. Every dollar figure must match a reported evidence value OF THE RIGHT
   METRIC AND FILM, within the precision the text implies. The metric is the
   axis named next to the figure ("$400M worldwide", "domestic total of
   $120M", "$280M overseas"); an unlabelled figure is read as the bound
   event's axis. A possessive or parenthetical title ("Beta Story's $100M",
   "Alpha Film ($120M)") ties the figure to that film. Sources: each film's
   reported gross on that axis, the international split (worldwide −
   domestic), the threshold and the gap on the event axis. A missing gross is
   never a valid source.
3b. Named people (directors, cast) must be in the evidence for the film they
   are attributed to ("Christopher Nolan's Alpha Film" needs Nolan in Alpha
   Film's credits); anyone else is an unsupported claim.
4. Headline length <= 85 characters (counted exactly; never truncated).
5. A shortened headline must keep the original's meaning (``meaning_changes``):
   the same bound films, the same film doing the passing, the same
   completed/anticipated status and the same figures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADLINE_MAX = 85

MONEY_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(billion|million|thousand|bn|b|m|k)?(?![\w])", re.I)
UNIT = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3, "": 1.0}

TIME_UNIT = r"(?:weekends?|weeks?|days?|frames?|months?|years?|outings?|sessions?)"
NUMBER_WORD = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a few|a couple of|a handful of)"
ORDINAL = r"\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
# Always a rank/record/superlative claim.
SUPERLATIVE_RE = re.compile(
    r"(?<![\w-])(no\.\s?\d+|#\s?\d+|number one|biggest|largest|highest(?:-grossing)?|top[- ]\d+|top-grossing|"
    r"records?|record-breaking|all-time|best|dethron\w*|unprecedented|history-making)(?![\w])", re.I)
# A claim unless the surrounding words make it ordinary language. (word pattern, allowed-context pattern
# matched at the word's start). Faramir 2.0 strategies will need real rankings/records/firsts; those come
# with ranked evidence, not by loosening these rules (docs/v2/STRATEGY_REQUIREMENTS.md, SR-1).
CONTEXTUAL = [
    (rf"(?:{ORDINAL})", rf"(?:{ORDINAL})[\s-]+(?:(?:straight|consecutive)\s+)?(?:{TIME_UNIT})(?![\w-])"
                        r"|first\s+(?:look|trailer|reviews?|reactions?)\b"),
    (r"at first", r"at first"),
    (r"only", rf"only\s+(?:\$|{NUMBER_WORD}\s+(?:more\s+)?{TIME_UNIT}\b|slightly\b|just\b|barely\b|a fraction\b)"),
    (r"ever", r"ever(?:\s+since\b|-\w)"),
    (r"most", r"most\s+of\b"),
    (r"leading", r"leading\s+(?:man|men|lady|ladies|role|roles|actor|actors|actress|star|stars)\b"),
]
_NOT_ONLY = re.compile(r"\bnot\s+$", re.I)
_AT_MOST = re.compile(r"\b(?:at|for the)\s+$", re.I)
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
    r")\s+(?:movie|film|flick|hit|sequel|franchise|title|blockbuster|release|classic|favorite|epic|entry|entries|"
    r"picture|feature|smash|juggernaut)s?(?![\w])", re.I)
RELATION_CATEGORY = {
    "franchise": {"franchise_sibling"},
    "same_director": {"creative_head_to_head_director"},
    "same_star": {"creative_head_to_head_cast"},
}
NEVER_SUPPORTED = {"rank", "record", "superlative", "chart_position", "first_or_only", "count"}
COMPARE_VERB_RE = re.compile(
    r"(?<![\w-])(pass(?:es|ed)?|surpass(?:es|ed)?|tops|topped|beat|beats|beaten|overtakes?|overtook|overtaken|"
    r"outgross(?:es|ed)?|eclips(?:es|ed)|outdid|outdoes|zooms past|blows past|moves past|nears?|nearing|"
    r"closing in on|closes in on|approach(?:es|ing)?|chas(?:es|ing)|eyes|eyeing|catches|caught)(?![\w])", re.I)
PASSIVE_AFTER_RE = re.compile(r"\s+by\b", re.I)
PASSIVE_BEFORE_RE = re.compile(r"\b(?:is|was|been|being|gets|got)\s+(?:\w+\s+)?$", re.I)
FALLS_RE = re.compile(r"(?<![\w-])(?:falls?|fell|slips?|slipped|trails?|trailing)\s+(?:to|behind)\b", re.I)


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


def title_variants(title: str) -> list[str]:
    """The full title plus a distinctive short form ("Mission: Impossible – Ghost Protocol" ->
    "Ghost Protocol", "Mission: Impossible") — what a shortened headline may use."""
    title = (title or "").strip()
    if not title:
        return []
    out = [title]
    for part in re.split(r"\s*[:–—]\s*|\s+-\s+", title):
        part = part.strip()
        if part and part != title and (len(part.split()) >= 2 or len(part) >= 7):
            out.append(part)
    return out


def _mask(text: str, titles: list[str]) -> str:
    """Blank out titles with same-length filler, so positions still line up with the raw text."""
    for t in sorted((t for t in titles if t), key=len, reverse=True):
        text = re.sub(re.escape(t), lambda m: "\u00b7" * len(m.group(0)), text, flags=re.I)
    return text


def _spans(text: str, films: dict[str, list[str]]) -> list[tuple[int, int, str]]:
    """[(start, end, film key)] for every non-overlapping mention, longest variant first."""
    taken, out = [], []
    variants = sorted(((v, k) for k, vs in films.items() for v in vs if v), key=lambda x: len(x[0]), reverse=True)
    for v, k in variants:
        for m in re.finditer(rf"(?<![\w]){re.escape(v)}(?![\w])", text, re.I):
            if not any(m.start() < e and s < m.end() for s, e in taken):
                taken.append((m.start(), m.end()))
                out.append((m.start(), m.end(), k))
    return sorted(out)


def _sentences(text: str) -> list[tuple[int, int]]:
    bounds, start = [], 0
    for m in re.finditer(r"[.!?]\s+|\n", text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return bounds


def _films(pick: dict) -> dict[str, list[str]]:
    ev = pick["evidence"]
    comp = ev.get("comp") or {}
    active_titles = {(ev.get("active") or {}).get("title") or "", pick.get("active_title") or ""}
    comp_titles = {comp.get("title") or "", pick.get("comp_title") or ""}
    films = {"active": [v for t in active_titles for v in title_variants(t)]}
    if ev.get("comp"):
        films["comp"] = [v for t in comp_titles for v in title_variants(t)]
    return films


def _completed_event(ev: dict) -> bool:
    event = ev.get("event") or {}
    if event.get("kind") == "event":
        return event.get("status") == "confirmed"
    return event.get("kind") == "just_passed" or (event.get("kind") == "milestone" and event.get("milestone_type") == "just_passed")


def _event_values(ev: dict) -> list[float]:
    vals = (ev.get("strategy_event") or {}).get("values") or {}
    out = []
    for v in vals.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out.append(float(v))
    return out


def check_claims(pick: dict) -> list[str]:
    ev = pick["evidence"]
    event, comp = ev.get("event") or {}, ev.get("comp")
    kind, axis, mtype = event.get("kind"), event.get("axis"), event.get("milestone_type")
    cats = set(ev.get("categories") or [])
    genres = {g.lower() for g in ((ev.get("active") or {}).get("genres") or []) + ((comp or {}).get("genres") or [])}
    reasons, event_claims = [], 0
    for c in pick.get("claims") or []:
        t = c.get("type")
        if t == "event":
            se = ev.get("strategy_event") or {}
            if (event.get("kind") == "event" and se.get("strategy") and
                    str(c.get("text") or "").strip() and se.get("status") in ("confirmed", "anticipated")):
                event_claims += 1
            else:
                reasons.append("claim_mismatch:event: no deliverable weekend/Discovery event is bound to this pick")
            continue
        if t == "chart_position" and chart_rank(pick) is not None:
            continue  # evidence-backed weekend rank (SR-1); the wording is checked by the lexical scan
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


RANK_RE = re.compile(r"(?:no\.\s?|#\s?)(\d+)|(number one)", re.I)
CHART_PHRASE_RE = re.compile(r"(?<![\w-])(box office crown|tops? the (?:weekend )?box office|wins? the weekend)(?![\w])", re.I)


def chart_rank(pick: dict) -> int | None:
    """The TN-reported weekend rank behind a weekend-strategy event (Takeover/Hold/Streak), if any.
    Rank language is supported only by this evidence (SR-1)."""
    se = (pick.get("evidence") or {}).get("strategy_event") or {}
    r = se.get("chart_rank")
    return int(r) if isinstance(r, (int, float)) or (isinstance(r, str) and r.isdigit()) else None


CAREER_RE = re.compile(r"\bcareer\b", re.I)


def career_record(pick: dict) -> bool:
    """A Star Record event backs career-record wording (SR-1): 'career-best', 'biggest … of her career'."""
    se = (pick.get("evidence") or {}).get("strategy_event") or {}
    return se.get("strategy") == "star_record"


def _superlatives(text: str, comp: dict, rank: int | None = None, career: bool = False) -> list[str]:
    for m in SUPERLATIVE_RE.finditer(text):
        word = m.group(1)
        if career and word.lower() in ("best", "biggest", "highest-grossing", "highest", "record") and \
                CAREER_RE.search(text[max(0, m.start() - 60):m.end() + 60]):
            continue  # evidence-backed career record
        if word.lower() == "best" and re.match(r"best\s+picture", text[m.start():], re.I) and comp.get("is_best_picture"):
            continue
        rm = RANK_RE.fullmatch(word.strip())
        if rm and rank is not None:
            stated = 1 if rm.group(2) else int(rm.group(1))
            if stated == rank:
                continue  # evidence-backed chart position (SR-1)
            return [f"rank_mismatch: {word!r} but the chart evidence says No. {rank}"]
        return [f"lexical_superlative: {word!r}"]
    for m in CHART_PHRASE_RE.finditer(text):
        if rank != 1:
            return [f"lexical_superlative: {m.group(1)!r} (needs No. 1 chart evidence)"]
    for word_re, allowed in CONTEXTUAL:
        for m in re.finditer(rf"(?<![\w-])({word_re})(?![\w])", text, re.I):
            rest, before = text[m.start():], text[:m.start()]
            if re.match(allowed, rest, re.I):
                continue
            w = m.group(1).lower()
            if (w == "only" and _NOT_ONLY.search(before)) or (w == "most" and _AT_MOST.search(before)):
                continue
            if w == "first" and re.search(r"\bat\s+$", before, re.I):
                continue
            return [f"lexical_superlative: {m.group(1)!r}"]
    return []


def _tense(text: str) -> str:
    completed, anticipated = bool(COMPLETED_RE.search(text)), bool(ANTICIPATED_RE.search(text))
    if completed and not anticipated:
        return "completed"
    if anticipated and not completed:
        return "anticipated"
    return "mixed" if completed else "none"


def direction_checks(raw: str, films: dict[str, list[str]]) -> list[str]:
    """The film right before the first comparison verb of a sentence is the one doing the passing;
    it must be the active film (passive "is passed by" / "falls behind" inverts the roles)."""
    if "comp" not in films:
        return []
    spans = _spans(raw, films)
    for s0, s1 in _sentences(raw):
        sentence = raw[s0:s1]
        verb = COMPARE_VERB_RE.search(sentence) or FALLS_RE.search(sentence)
        if not verb:
            continue
        v0 = s0 + verb.start()
        before = [k for a, b, k in spans if s0 <= a and b <= v0]
        if not before:
            continue
        subject = before[-1]
        passive = bool(FALLS_RE.match(sentence, verb.start())) or (
            bool(PASSIVE_AFTER_RE.match(sentence, verb.end())) and bool(PASSIVE_BEFORE_RE.search(sentence[:verb.start()])))
        passer = ("comp" if subject == "active" else "active") if passive else subject
        if passer != "active":
            return [f"direction_reversed: {sentence.strip()!r} has the comparison film passing the active film"]
    return []


def genre_checks(raw: str, masked: str, pick: dict, films: dict[str, list[str]]) -> list[str]:
    """Attribute each genre descriptor to a film, then require that film to have the genre.

    Attribution: a bound title right after the phrase ("the horror film Smile") or in apposition
    right before it ("Smile, the horror hit"); otherwise, in a sentence with a comparison verb,
    a phrase before the verb describes the subject and one after it the object (passive inverts);
    with no such context it describes the active film. Milestones only have the active film.
    """
    ev = pick["evidence"]
    genres = {"active": {g.lower() for g in ((ev.get("active") or {}).get("genres") or [])},
              "comp": {g.lower() for g in ((ev.get("comp") or {}).get("genres") or [])}}
    spans = _spans(raw, films)
    reasons = []
    for m in GENRE_RE.finditer(masked):
        genre = GENRE_WORDS[m.group(1).lower()]
        film = "active"
        if "comp" in films:
            s0, s1 = next((a, b) for a, b in _sentences(raw) if a <= m.start() <= b)
            after = [k for a, b, k in spans if m.end() <= a <= m.end() + 2]
            appos = [k for a, b, k in spans if s0 <= a and re.fullmatch(r",\s*(?:the|a|an)\s+", raw[b:m.start()] or "x", re.I)]
            if after:
                film = after[0]
            elif appos:
                film = appos[-1]
            else:
                sentence = raw[s0:s1]
                verb = COMPARE_VERB_RE.search(sentence) or FALLS_RE.search(sentence)
                if verb:
                    passive = bool(FALLS_RE.match(sentence, verb.start())) or (
                        bool(PASSIVE_AFTER_RE.match(sentence, verb.end()))
                        and bool(PASSIVE_BEFORE_RE.search(sentence[:verb.start()])))
                    object_side = m.start() >= s0 + verb.end()
                    film = "comp" if object_side != passive else "active"
        if genre in genres[film]:
            continue
        other = "comp" if film == "active" else "active"
        if genre in genres.get(other, set()):
            reasons.append(f"genre_misattributed: {m.group(0)!r} describes the {film} film; {genre} is the {other} film's genre")
        else:
            reasons.append(f"genre_unsupported: {m.group(0)!r} (films are {sorted(genres['active'] | genres['comp'])})")
    return reasons


# Capitalized words that open or fill Title Case headlines but are not people. A run of these is
# never treated as a person's name.
NOT_A_NAME = set("""
a an the of in at on to for with and or but by from as now just new its it this that his her their our
passes passed crosses crossed tops topped beats nears closing about set poised could moves overtakes edges surges
film films movie movies sequel franchise series hit hits box office weekend weekends opening debut run total
domestic worldwide global international overseas year years month week day days season holiday holidays
summer fall autumn winter spring halloween thanksgiving christmas easter labor memorial independence
record records biggest best top latest original first second third final part chapter vol volume
billion million studio studios theater theaters theatrical release releases fans audiences critics
why how what when where who which still again finally officially quietly already more most less
""".split())
PERSON_ROLE_RE = re.compile(
    r"(?<![\w-])(?:directed by|director|filmmaker|helmer|writer-director|auteur|starring|stars|star|led by|"
    r"headlined by|fronted by|actor|actress|from director|from filmmaker)\s+", re.I)
_NAME_TOKEN = r"[A-Z][a-zA-Z'’.\-]*[a-zA-Z.]"
_NAME_RE = re.compile(rf"{_NAME_TOKEN}(?:\s+(?:{_NAME_TOKEN}|de|da|del|van|von|der|le|la|di))*")


def _people(entity: dict | None) -> list[str]:
    if not entity:
        return []
    out = [d.strip() for d in str(entity.get("director") or "").split(",") if d.strip()]
    out += [str(c).strip() for c in (entity.get("lead_cast") or []) if str(c).strip()]
    return out


def _same_person(name: str, person: str) -> bool:
    n, p = name.casefold().strip().rstrip("."), person.casefold().strip()
    return n == p or (" " not in n and p.split() and p.split()[-1] == n)


def _trim_name(tokens: list[str]) -> list[str]:
    """Drop headline words from both ends; what remains (1-4 tokens) is a candidate name."""
    while tokens and tokens[0].casefold().strip(".") in NOT_A_NAME:
        tokens = tokens[1:]
    while tokens and tokens[-1].casefold().strip(".") in NOT_A_NAME:
        tokens = tokens[:-1]
    if not tokens or len(tokens) > 4 or any(t.casefold().strip(".") in NOT_A_NAME for t in tokens):
        return []
    return tokens


def person_checks(raw: str, masked: str, pick: dict, films: dict[str, list[str]]) -> list[str]:
    """Every person named as a film's director/star/talent must be in that film's evidence.

    Detected: a possessive name ("Christopher Nolan's", or a one-word "Nolan's" directly before a
    bound title), a role cue ("directed by X", "starring X") and "X-led"/"X-directed". The film is
    the bound title right after the name, else the film the sentence is about (the active film).
    A comparison film's credits count only when its identity is verified.
    """
    ev = pick["evidence"]
    comp = ev.get("comp")
    people = {"active": _people(ev.get("active")),
              "comp": _people(comp) if (comp or {}).get("identity_status") == "verified" else []}
    spans = _spans(raw, films)
    found: list[tuple[str, str | None]] = []
    for m in re.finditer(r"((?:" + _NAME_TOKEN + r"\s+){0,4}" + _NAME_TOKEN + r")['’]s(?![\w])", masked):
        tokens = _trim_name(m.group(1).split())
        if not tokens:
            continue
        nxt = masked[m.end():m.end() + 2]
        before_title = nxt.strip().startswith("\u00b7")
        if len(tokens) == 1 and not before_title:
            continue  # a lone capitalized possessive not attached to a title is not treated as a person
        film = next((k for a, b, k in spans if a >= m.end() and a <= m.end() + 2), None) if before_title else None
        found.append((" ".join(tokens), film))
    for m in PERSON_ROLE_RE.finditer(masked):
        nm = _NAME_RE.match(masked, m.end())
        tokens = _trim_name(nm.group(0).split()) if nm else []
        if tokens:
            found.append((" ".join(tokens), None))
    for m in re.finditer(r"(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})-(?:led|directed|fronted|starring|headlined)(?![\w])", masked):
        tokens = _trim_name(m.group(1).split())
        if tokens:
            found.append((" ".join(tokens), None))
    reasons = []
    for name, film in found:
        target = film or "active"
        if any(_same_person(name, p) for p in people.get(target, [])):
            continue
        if not film and any(_same_person(name, p) for p in people.get("comp", [])):
            continue  # a role cue without a title: either bound film's credits support it
        other = "comp" if target == "active" else "active"
        if any(_same_person(name, p) for p in people.get(other, [])):
            reasons.append(f"person_misattributed: {name!r} is credited on the {other} film, not the {target} film")
        else:
            reasons.append(f"person_unsupported: {name!r} is not in the evidence for the {target} film's "
                           "director or cast")
    return reasons


def lexical_checks(pick: dict, other_titles: list[str]) -> list[str]:
    ev = pick["evidence"]
    comp = ev.get("comp") or {}
    films = _films(pick)
    own = [v for vs in films.values() for v in vs]
    raw = f"{pick.get('headline', '')}\n{pick.get('angle', '')}"
    text = _mask(raw, own)
    reasons = _superlatives(text, comp, chart_rank(pick), career_record(pick))
    tense = _tense(text)
    if _completed_event(ev):
        if tense == "anticipated":
            reasons.append("tense_mismatch: anticipatory wording for an event that already happened")
    elif tense == "completed":
        reasons.append("tense_mismatch: completed wording for an event that has not happened yet")
    reasons += direction_checks(raw, films)
    reasons += genre_checks(raw, text, pick, films)
    reasons += person_checks(raw, text, pick, films)
    own_lower = [o.lower() for o in own if o]
    for t in other_titles:
        if not t or any(t.lower() in o for o in own_lower):
            continue
        if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", text):
            reasons.append(f"third_film_named: {t!r}")
            break
    return reasons


AXIS_LABELS = [
    ("international", r"international(?:ly)?|overseas|abroad|foreign"),
    ("worldwide", r"worldwide|world-wide|global(?:ly)?|around the world|across the globe"),
    ("domestic", r"domestic(?:ally)?|stateside|north american?|in north america|(?:in the )?u\.?s\.?(?: and canada)?"),
]
_AXIS_RE = re.compile(r"(?<![\w-])(" + "|".join(p for _, p in AXIS_LABELS) + r")(?![\w-])", re.I)
_AXIS_AFTER_WORDS = 4   # "$400M worldwide", "$400 million at the domestic box office"
_AXIS_BEFORE_WORDS = 5  # "domestic total of $120M", "a worldwide haul of $400M"


def _axis_of(word: str) -> str:
    for axis, pattern in AXIS_LABELS:
        if re.fullmatch(pattern, word, re.I):
            return axis
    return ""


def _figure_axis(text: str, start: int, end: int, s0: int, s1: int) -> str | None:
    """The axis named next to a figure (after it first, then before it), within its own clause."""
    after = text[end:s1]
    after = re.split(r"\$|[;:]", after)[0]
    words = after.split()[:_AXIS_AFTER_WORDS]
    m = _AXIS_RE.search(" ".join(words))
    if m:
        return _axis_of(m.group(1))
    before = re.split(r"\$|[;:]", text[s0:start])[-1]
    words = before.split()[-_AXIS_BEFORE_WORDS:]
    hits = list(_AXIS_RE.finditer(" ".join(words)))
    return _axis_of(hits[-1].group(1)) if hits else None


def _figure_film(raw: str, start: int, s0: int, spans) -> str | None:
    """A film the figure is explicitly tied to: "Title's [words] $X" or "Title ($X"."""
    before = [(a, b, k) for a, b, k in spans if s0 <= a and b <= start]
    if not before:
        return None
    a, b, k = before[-1]
    between = raw[b:start]
    if re.fullmatch(r"['’]s\s+(?:[\w-]+\s+){0,3}", between) or re.fullmatch(r"\s*\(\s*", between):
        return k
    return None


def _film_values(ev: dict) -> dict[str, dict[str, float]]:
    """{film: {axis: value}} from reported evidence only (international = worldwide − domestic)."""
    out = {}
    for key, getter in (("active", lambda a: _active(ev, a)), ("comp", lambda a: _reported(ev.get("comp"), a))):
        if key == "comp" and not ev.get("comp"):
            continue
        vals = {a: getter(a) for a in ("domestic", "worldwide")}
        if vals["domestic"] and vals["worldwide"] and vals["worldwide"] >= vals["domestic"]:
            vals["international"] = vals["worldwide"] - vals["domestic"]
        out[key] = {a: float(v) for a, v in vals.items() if v}
    return out


def figure_checks(pick: dict) -> list[str]:
    ev = pick["evidence"]
    if (ev.get("event") or {}).get("kind") == "event":
        # Weekend/Discovery event: every figure must be one of the event's stored values.
        known = _event_values(ev)
        return [f"unverified_figure: {txt.strip()} matches no value of the bound event"
                for value, tol, txt in parse_money(f"{pick.get('headline', '')} {pick.get('angle', '')}")
                if not any(abs(value - k) <= tol + 1e-6 for k in known)]
    comp = ev.get("comp")
    event = ev.get("event") or {}
    event_axis = event.get("axis")
    reasons = []
    if comp:
        dom, ww = _reported(comp, "domestic"), _reported(comp, "worldwide")
        if dom and ww and ww < dom:
            reasons.append("comp_partial_run: comparison worldwide < domestic")
        if _reported(comp, event_axis) is None:
            reasons.append(f"comp_metric_unavailable: comparison {event_axis} gross is not reported")
    films = _films(pick)
    values = _film_values(ev)
    names = {"active": (ev.get("active") or {}).get("title") or pick.get("active_title") or "active film",
             "comp": (comp or {}).get("title") or pick.get("comp_title") or "comparison film"}
    # Every evidence value, keyed by (source, axis): each film's gross per axis, the gap between the films
    # per axis, and the event's threshold and gap to it.
    known: dict[tuple[str, str], float] = {}
    for k, by_axis in values.items():
        for a, v in by_axis.items():
            known[(k, a)] = v
    for a in ("domestic", "worldwide", "international"):
        if ("active", a) in known and ("comp", a) in known:
            known[("gap", a)] = abs(known[("active", a)] - known[("comp", a)])
    if event.get("threshold") and event_axis:
        known[("threshold", event_axis)] = float(event["threshold"])
        current = _active(ev, event_axis)
        if current and not comp:
            known[("gap", event_axis)] = abs(float(event["threshold"]) - float(current))
    label = {"active": names["active"], "comp": names["comp"], "gap": "the gap", "threshold": "the threshold"}
    for field_name in ("headline", "angle"):
        raw = pick.get(field_name) or ""
        spans = _spans(raw, films)
        for s0, s1 in _sentences(raw):
            for m in MONEY_RE.finditer(raw, s0, s1):
                (value, tol, txt), = parse_money(m.group(0))
                labelled = _figure_axis(raw, m.start(), m.end(), s0, s1)
                axis = labelled or event_axis
                film = _figure_film(raw, m.start(), s0, spans)
                allowed = {key: v for key, v in known.items()
                           if key[1] == axis and (key[0] == film if film else True)}
                if any(abs(value - v) <= tol + 1e-6 for v in allowed.values()):
                    continue
                hits = [key for key, v in known.items() if abs(value - v) <= tol + 1e-6]
                elsewhere = [f"{label[k]} {a}" for k, a in hits]
                where = f" ({txt.strip()} is {', '.join(elsewhere)})" if elsewhere else ""
                scope = f"{names[film]}'s " if film else ""
                if film and any(k in ("active", "comp") and a == axis and k != film for k, a in hits):
                    reasons.append(f"figure_misattributed: {txt.strip()} is attributed to {names[film]} but is "
                                   f"{', '.join(elsewhere)}")
                elif elsewhere and not labelled:
                    reasons.append(f"figure_axis_unlabelled: {txt.strip()} read as {scope}{axis} (the event axis) "
                                   f"but matches no {axis} evidence{where}; label its metric")
                elif elsewhere:
                    reasons.append(f"figure_misattributed: {txt.strip()} labelled {scope}{axis} matches no "
                                   f"{scope}{axis} evidence{where}")
                else:
                    reasons.append(f"unverified_figure: {txt.strip()} matches no reported evidence value")
    return reasons


def length_check(pick: dict) -> list[str]:
    n = len(pick.get("headline") or "")
    return [f"headline_too_long: {n} > {HEADLINE_MAX} characters"] if n > HEADLINE_MAX else []


def meaning_changes(pick: dict, new_headline: str) -> list[str]:
    """What a shortened headline changed relative to the original. Empty = same meaning.

    Same bound films named (a distinctive short form of a title counts), the same film doing the
    passing, the same completed/anticipated wording, and the same dollar figures (each figure in
    either headline must match one in the other within the coarser stated precision).
    """
    old = pick.get("headline") or ""
    films = _films(pick)
    reasons = []
    named_old = {k for _, _, k in _spans(old, films)}
    named_new = {k for _, _, k in _spans(new_headline, films)}
    for k in sorted(named_old - named_new):
        reasons.append(f"meaning_changed: the {k} film is no longer named")
    own = [v for vs in films.values() for v in vs]
    t_old, t_new = _tense(_mask(old, own)), _tense(_mask(new_headline, own))
    if t_old != t_new:
        reasons.append(f"meaning_changed: status wording went from {t_old} to {t_new}")
    if not direction_checks(old, films) and direction_checks(new_headline, films):
        reasons.append("meaning_changed: the comparison direction was reversed")
    f_old, f_new = parse_money(old), parse_money(new_headline)
    def covered(a, bs):
        return any(abs(a[0] - b[0]) <= max(a[1], b[1]) + 1e-6 for b in bs)
    for f in f_old:
        if not covered(f, f_new):
            reasons.append(f"meaning_changed: figure {f[2].strip()} was dropped or changed")
    for f in f_new:
        if not covered(f, f_old):
            reasons.append(f"meaning_changed: figure {f[2].strip()} was added")
    return reasons


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
