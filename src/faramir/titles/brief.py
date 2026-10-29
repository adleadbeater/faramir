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
    cerebro_block: str = ""
    power_block: str = ""

    def user_message(self) -> str:
        parts = [f"{self.draft} | {self.context}"]
        if self.cerebro_block:
            parts.append(self.cerebro_block)
        if self.power_block:
            parts.append(self.power_block)
        parts.append("ARTICLE BODY (read this FIRST, reframe must stay true to it):\n" + self.fact_sheet)
        return "\n\n".join(parts)


def _status(cand: dict) -> str:
    if cand.get("strategy") == "discovery":
        return "confirmed"   # Discovery figures are verified chart rows; "proposed" refers to the pattern, not the facts
    if cand.get("status") in STATUS_WORDS:
        return cand["status"]
    return "anticipated" if cand.get("kind") in ("about_to_pass", "milestone") and cand.get("strategy") != "overtake" \
        else "confirmed"


def build(card_film: dict, cand: dict, credits: dict, data_date: str, weekend: str | None, sources: list[str],
          pool: list, baseline_sa: int, phrases: list) -> Brief:
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
             + (f" · franchise: {collection}" if collection else ""), "VERIFIED FIGURES:"]
    if cand.get("kind") != "event":
        dom, ww = card_film.get("domestic_today"), card_film.get("worldwide_today")
        lines.append(f"- {title}: {money(dom)} domestic total through {data_date}"
                     + (f"; {money(ww)} worldwide" if ww else "") + " (The Numbers daily chart; Box Office Mojo)")
        figures += [x for x in (dom, ww, card_film.get("domestic_yesterday"), card_film.get("worldwide_yesterday")) if x]
        if comp:
            dom_l, ww_l = comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")
            lines.append(f"- Comparison: '{comp['title']}' ({comp.get('release_year')}): "
                         f"{money(dom_l) + ' domestic, ' if dom_l else ''}{money(ww_l) + ' worldwide ' if ww_l else ''}"
                         f"lifetime (MovieWeb's comparison corpus)")
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
    people = list(dict.fromkeys(p for p in [director, *cast] if p))
    lines.append("MAY BE NAMED: films: " + "; ".join(f"'{n}'" for n in dict.fromkeys(names))
                 + (f"; people: {', '.join(people)}" if people else ""))
    lines.append("SOURCES: " + " · ".join(sources))
    rank_evidence = {"chart_rank": v.get("rank"), "tier": v.get("tier"), "weekends": v.get("weekends")}
    opening = v.get("week_number") == 1 or "opening weekend" in (cand.get("rationale") or "").lower()
    if opening:
        lines.insert(4, "- This is the film's OPENING weekend.")

    d = data.demand(pool, [title, director, *cast, collection, comp.get("title") or ""],
                    [data.GENRE_TAGS.get(g, g) for g in genres[:2]] + ["Box Office"])
    draft = _draft(strategy, title, comp, cand, v)
    return Brief(strategy, status, card_film, draft, (cand.get("rationale") or "").split(" · sources:")[0],
                 "\n".join(lines), figures, names, people, rank_evidence, opening=opening,
                 cerebro_block=data.demand_block(d, baseline_sa), power_block=data.power_words_block(phrases, baseline_sa))


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
