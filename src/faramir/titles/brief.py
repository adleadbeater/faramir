"""The title brief for one story: Faramir's verified fact sheet plus the MovieWeb evidence, in the
input format Aragorn's prompt expects ("draft | context", Cerebro block, Power Words block, ARTICLE BODY).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from faramir.strategies.common import money
from faramir.titles import data

WHAT_IT_IS = {
    "overtake": "the film's total just passed another film's lifetime gross (or a round milestone)",
    "chase": "the film is about to pass a well-known film's lifetime gross",
    "countdown": "the film is about to cross a round-number milestone",
    "takeover": "a new No. 1 at the weekend box office",
    "hold": "the No. 1 film held the top spot against a strong new opener",
    "streak": "consecutive weekends near the top of the chart",
    "legs": "an unusually small weekend-to-weekend drop for its stage of release",
    "exit": "the film is losing theaters fast, or its run has ended",
    "dark_horse": "a smaller release out-earned a bigger one at the same stage",
    "discovery": "an unusual pattern on the weekend chart (proposed; verified figures, needs editorial judgement)",
}
INTERNAL = {"collection_status", "minimum", "weekends_checked", "cohort", "scale_advantage"}
LABELS = {"rank": "chart rank", "margin_over_second": "lead", "previous_leader": "previous No. 1",
          "second_place": "No. 2 this weekend", "second_gross": "No. 2 weekend gross", "rival": "rival film",
          "tier": "streak tier (top N)", "weekends": "consecutive weekends"}
STATUS_WORDS = {"confirmed": "HAPPENED", "anticipated": "ANTICIPATED (has not happened yet)",
                "provisional": "PROVISIONAL (Sunday estimates; may change)"}


@dataclass
class Brief:
    strategy: str
    status: str
    film: dict
    draft: str
    context: str
    fact_sheet: str
    figures: list[float]
    names: list[str]
    people: list[str]
    rank_evidence: dict = field(default_factory=dict)
    opening: bool = False
    genres: list = field(default_factory=list)
    film_genre_phrase: str | None = None
    film_genre_options: list = field(default_factory=list)
    comp_title: str | None = None
    kits: list = field(default_factory=list)       # data.film_kit per film: [this film, then the others]
    genre_block: str = ""
    verb_options: list = field(default_factory=list)
    descriptor_facts: list = field(default_factory=list)
    cerebro_block: str = ""
    power_block: str = ""
    baseline: int = 0

    def user_message(self) -> str:
        parts = [f"{self.draft} | {self.context}"]
        if self.cerebro_block:
            parts.append(self.cerebro_block)
        if self.power_block:
            parts.append(self.power_block)
        if self.genre_block:
            parts.append(self.genre_block)
        if self.kits:
            parts.append(data.kits_block(self.kits, self.baseline))
        parts.append("ARTICLE BODY (read this FIRST, reframe must stay true to it):\n" + self.fact_sheet)
        required = []
        if self.film_genre_phrase:
            top = self.film_genre_options[0]
            required.append(f"- When you name {self.film['title']}'s genre, use \"{self.film_genre_phrase}\": its best-performing "
                            f"accurate genre wording on MovieWeb (S/A ≈ {top['sa']:,}, {top['n']} uses). Don't swap in a "
                            "weaker genre word or blend genres (\"Action-Adventure Thriller\").")
        required.append("- Every title must include \"Box Office\".")
        allowed = [o for o in self.verb_options if o["allowed"]]
        if allowed:
            listing = "; ".join(f"\"{o['phrase']}\" (S/A ≈ {o['sa']:,}, {o['n']} uses, {o['basis']})" for o in allowed)
            weak = [o["phrase"] for o in self.verb_options if not o["allowed"]]
            required.append(f"- Verb: use one of the strongest proven verbs that are accurate for this story, with the other film as "
                            f"its object (\"[film] Eyes [the other film, described] at the Box Office\"): {listing}. "
                            f"Not: {', '.join(weak)} (weaker or never used in MovieWeb News titles)." if weak else
                            f"- Verb: use one of the strongest proven verbs that are accurate for this story: {listing}.")
        per_film = [f"{k['title']}: {', '.join(k['supported']) or 'none'}" for k in self.kits]
        unsupported = [x for x in ("Raunchy", "Gritty", "Steamy", "Blockbuster", "Hit")
                       if x not in data.supported_descriptors(self.descriptor_facts)][:3]
        required.append("- Descriptors (tone, franchise, studio, success words like \"Hit\" or \"Major\") must be backed by "
                        "TMDB or the figures, and describe the film they're backed for. Supported: " + "; ".join(per_film)
                        + ". Any other descriptor" + (f" ({', '.join(repr(x) for x in unsupported)}...)" if unsupported else "")
                        + " is rejected. Genre wording is covered above.")
        lead = self.kits[0] if self.kits else None
        if lead and not lead["name_title"]:
            required.append(f"- '{self.film['title']}' has little MovieWeb demand of its own (S/A ≈ {lead['title_demand']['sa']:,}): lead "
                            "with one of its ready-made descriptions instead of its title.")
        for k in self.kits[1:]:
            if k["required"]:
                first = kit_first(k)
                required.append(f"- Reference {k['role'].lower()} '{k['title']}' as strongly as the lead film, so the reader can tell "
                                f"which film it is: {first}." + ("" if k["name_title"] else " Not its bare title, not a bare category."))
        if self.strategy == "discovery":
            required.append("- Payload: say what happened in words a reader can picture, with its time frame and why it's unusual "
                            "(\"Grows 25% in Its Second Weekend\" when films that week usually drop). A bare percentage means nothing.")
        if required:
            parts.append("REQUIRED FOR THIS STORY (house rules; titles that break them are rejected):\n" + "\n".join(required))
        return "\n\n".join(parts)


def kit_first(k: dict) -> str:
    d = data.kit_descriptions(k)
    return " or ".join(x.split(": ", 1)[1].split(" (")[0] for x in d[:2]) if d else f"its people or its gross"


def _status(cand: dict) -> str:
    if cand.get("strategy") == "discovery":
        return "confirmed"   # Discovery figures are verified chart rows; "proposed" refers to the pattern, not the facts
    if cand.get("status") in STATUS_WORDS:
        return cand["status"]
    return "anticipated" if cand.get("kind") in ("about_to_pass", "milestone") and cand.get("strategy") != "overtake" \
        else "confirmed"


def build(card_film: dict, cand: dict, credits: dict, data_date: str, weekend: str | None, sources: list[str],
          pool: list, baseline_sa: int, phrases: list, comp_credits: dict | None = None,
          other_credits: dict | None = None) -> Brief:
    """other_credits: TMDB credits for the other films the story names (previous No. 1, rival), by title."""
    strategy = cand.get("strategy") or "overtake"
    status = _status(cand)
    title = card_film["title"]
    genres = credits.get("genres") or card_film.get("genres") or []
    director = credits.get("director") or card_film.get("director") or ""
    cast = (credits.get("cast") or card_film.get("lead_cast") or [])[:3]
    collection = credits.get("collection") or card_film.get("collection_name") or ""
    comp = cand.get("comp") or {}
    v = cand.get("event_values") or {}
    figures: list[float] = []
    lines = [f"STORY TYPE: {strategy.replace('_', ' ').title()}: {WHAT_IT_IS.get(strategy, strategy)}.",
             f"STATUS: {STATUS_WORDS.get(status, status)}.",
             f"FILM: {title}" + (f" · genres (TMDB): {', '.join(genres)}" if genres else "")
             + (f" · director: {director}" if director else "") + (f" · top-billed cast: {', '.join(cast)}" if cast else "")
             + (f" · franchise: {collection}" if collection else "")
             + (f" · rated {credits['certification']}" if credits.get("certification") else "")
             + (f" · studios: {', '.join(credits['companies'][:4])}" if credits.get("companies") else "")
             + (f" · TMDB keywords: {', '.join(credits['keywords'][:15])}" if credits.get("keywords") else ""),
             "VERIFIED FIGURES:"]
    if cand.get("kind") != "event":
        dom, ww = card_film.get("domestic_today"), card_film.get("worldwide_today")
        lines.append(f"- {title}: {money(dom)} domestic total through {data_date}"
                     + (f"; {money(ww)} worldwide" if ww else "") + " (The Numbers daily chart; Box Office Mojo)")
        figures += [x for x in (dom, ww, card_film.get("domestic_yesterday"), card_film.get("worldwide_yesterday")) if x]
        if comp:
            dom_l, ww_l = comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")
            comp_genres = ", ".join(comp.get("genres") or [])
            honours = ", ".join(x for x, on in (("Best Picture winner", comp.get("is_best_picture")),
                                                ("classic", comp.get("is_classic"))) if on)
            lines.append(f"- Comparison: '{comp['title']}' ({comp.get('release_year')}): "
                         f"{money(dom_l) + ' domestic, ' if dom_l else ''}{money(ww_l) + ' worldwide ' if ww_l else ''}"
                         f"lifetime (MovieWeb's comparison corpus)"
                         + (f" · genres: {comp_genres}" if comp_genres else "") + (f" · {honours}" if honours else ""))
            figures += [x for x in (comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")) if x]
        if cand.get("threshold_value"):
            if not comp:
                lines.append(f"- Milestone: {money(cand['threshold_value'])} {cand.get('axis')}")
            figures.append(cand["threshold_value"])
            current = ww if cand.get("axis") == "worldwide" else dom
            if current and cand.get("kind") in ("about_to_pass", "milestone") and cand["threshold_value"] > current:
                gap = cand["threshold_value"] - current
                lines.append(f"- Gap still to go: {money(gap)}")
                figures.append(gap)
        lines.append(f"- Axis: {cand.get('axis')}")
    else:
        when = f"weekend of {weekend}" if weekend else "this weekend"
        for k, val in v.items():
            if val is None or isinstance(val, (list, dict)) or k.endswith("_key") or k in INTERNAL:
                continue
            label = LABELS.get(k, k.replace("_", " "))
            if "gross" in k and isinstance(val, (int, float)):
                shown = money(val)
            elif k in ("margin_over_second",):
                shown = f"{val:.0%} ahead of No. 2"
            elif isinstance(val, (int, float)) and "theaters" in k:
                shown = f"{val:,.0f}"
            else:
                shown = val
            lines.append(f"- {label}: {shown}")
            if isinstance(val, (int, float)):
                figures.append(float(val))
        if v.get("collection_status") == "provisional":
            lines.append("- NOTE: Sunday estimates; the ranking could still change")
        lines.append(f"- Chart: The Numbers weekend chart, {when}")
    lines.append(f"FARAMIR'S SUMMARY: {cand.get('rationale') or ''}")
    names = [title] + [n for n in (comp.get("title"), v.get("previous_leader"), v.get("rival"), v.get("second_place")) if n]
    kits = _kits(strategy, title, credits, card_film, comp, comp_credits, v, other_credits or {}, pool, phrases)
    people = list(dict.fromkeys(p for p in [director, *cast, *[e["name"] for k in kits[1:] for e in k["people"]]] if p))
    for k in kits[1:]:
        if k["people"] or k["franchise"]:
            lines.append(f"- {k['title']}: " + "; ".join(x for x in (
                f"top-billed / director: {', '.join(e['name'] for e in k['people'])}" if k["people"] else "",
                f"franchise: {k['franchise']}" if k["franchise"] else "") if x))
    lines.append("MAY BE NAMED: films: " + "; ".join(f"'{n}'" for n in dict.fromkeys(names))
                 + (f"; people: {', '.join(people)}" if people else ""))
    lines.append("SOURCES: " + " · ".join(sources))
    rank_evidence = {"chart_rank": v.get("rank"), "tier": v.get("tier"), "weekends": v.get("weekends"),
                     "previous_leader": v.get("previous_leader")}
    opening = v.get("week_number") == 1 or "opening weekend" in (cand.get("rationale") or "").lower()
    if opening:
        lines.insert(4, "- This is the film's OPENING weekend.")

    film_opts = data.genre_options(genres, pool, phrases)
    comp_opts = data.genre_options(comp.get("genres") or [], pool, phrases) if comp else []
    d = data.demand(pool, [title, director, *cast, collection, comp.get("title") or ""],
                    [data.GENRE_TAGS.get(g, g) for g in genres[:2]] + ["Box Office"])
    draft = _draft(strategy, title, comp, cand, v)
    return Brief(strategy, status, card_film, draft, (cand.get("rationale") or "").split(" · sources:")[0],
                 "\n".join(lines), figures, names, people, rank_evidence, opening=opening, genres=genres,
                 film_genre_phrase=film_opts[0]["phrase"] if film_opts else None, film_genre_options=film_opts,
                 comp_title=comp.get("title"), kits=kits, baseline=baseline_sa,
                 genre_block=data.genre_block(title, film_opts, comp.get("title"), comp_opts),
                 verb_options=data.verb_options(data.verb_group(strategy, status, bool(comp)), pool, phrases),
                 descriptor_facts=[k["facts"] for k in kits],
                 cerebro_block=data.demand_block(d, baseline_sa), power_block=data.power_words_block(phrases, baseline_sa))


def _kits(strategy, title, credits, card_film, comp, comp_credits, v, other, pool, phrases) -> list[dict]:
    """The film itself, then each other film the story needs the reader to recognise."""
    def kit(role, name, cr, gross, honour, required):
        cr = cr or {}
        facts = data.film_facts(cr, *gross)
        k = data.film_kit(name, cr, pool, phrases, gross[1] or gross[0], honour, facts)
        k.update(role=role, required=required, supported=data.supported_descriptors([facts]))
        return k
    out = [kit("THIS FILM", title, credits, (card_film.get("domestic_today"), card_film.get("worldwide_today")), "", True)]
    if comp:
        honour = "Best Picture-Winning" if comp.get("is_best_picture") else "Classic" if comp.get("is_classic") else ""
        cr = {**(comp_credits or {}), "genres": comp.get("genres") or (comp_credits or {}).get("genres") or []}
        out.append(kit("THE FILM IT'S PASSING" if strategy != "overtake" else "THE FILM IT PASSED", comp["title"], cr,
                       (comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")), honour, True))
    for key, role, required in (("previous_leader", "THE FILM IT REPLACED AT NO. 1", strategy == "takeover"),
                                ("rival", "THE FILM IT OUT-EARNED", strategy == "dark_horse")):
        if v.get(key):
            out.append(kit(role, v[key], other.get(v[key]), (None, None), "", required))
    return out


def _draft(strategy, title, comp, cand, v) -> str:
    if strategy == "overtake" and comp:
        return f"{title} passes {comp['title']} at the box office"
    if strategy == "chase" and comp:
        return f"{title} is about to pass {comp['title']} at the box office"
    if strategy == "countdown" and cand.get("threshold_value"):
        return f"{title} nears {money(cand['threshold_value'])} at the box office"
    if strategy == "takeover":
        return f"{title} takes No. 1 at the box office"
    if strategy == "dark_horse" and v.get("rival"):
        return f"{title} beats {v['rival']} at the box office"
    return f"{title} box office update"
