"""What connects a film to a classic it is chasing or has passed (the "Modern vs. Classic" story).

A comparison between a modern film and an old one only reads as a story when there is a real through-line:
the same franchise, director or top-billed actor, the same main genre, or a shared theme (TMDB keywords).
This module names that link, and adds MovieWeb's own audience evidence for the people involved: a classic
starring someone whose News coverage reliably over-performs on MovieWeb is a stronger story for MovieWeb
readers than one starring an actor they don't click on.

Everything here is evidence, never a guess: no TMDB keywords or no Cerebro signal means no theme link or
no demand line, and the card says only what was found.
"""

from __future__ import annotations

from typing import Callable

# Keywords too generic to count as a shared theme (production notes, formats, sequel markers).
GENERIC_KEYWORDS = {
    "based on novel or book", "based on novel", "based on true story", "based on a true story", "sequel", "remake",
    "duringcreditsstinger", "aftercreditsstinger", "woman director", "independent film", "murder", "friendship",
    "love", "family", "violence", "revenge", "new york city", "los angeles, california", "usa", "united states of america",
    "based on comic", "superhero", "male protagonist", "female protagonist", "ensemble cast", "cult film", "3d",
    "imax", "anime", "live action and animation", "short film", "mockumentary", "documentary", "musical",
}
MIN_SHARED_KEYWORDS = 2
MAX_COMP_CAST = 5                     # the classic's billed cast checked for audience demand
DEMAND_STRENGTH = {"high": 0.1, "medium": 0.05}   # added to the event's strength when the classic carries a proven draw
LINK_STRENGTH = 0.1                   # a classic with a real link ranks above one that is only "notable"

KINDS = ("franchise", "director", "cast", "genre", "theme")


def _names(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [x for x in (v or []) if x]


def comp_people(comp: dict) -> list[str]:
    return list(dict.fromkeys(_names(comp.get("director")) + _names(comp.get("lead_cast"))[:MAX_COMP_CAST]))


def candidate_names(film: dict, comp: dict) -> list[str]:
    """Every name the audience lookup should cover for this pair."""
    return list(dict.fromkeys(comp_people(comp) + _names(film.get("director")) + _names(film.get("lead_cast"))[:3]))


def theme_keywords(film_kw: list[str] | None, comp_kw: list[str] | None) -> list[str]:
    a = {k.lower(): k for k in (film_kw or []) if k and k.lower() not in GENERIC_KEYWORDS}
    b = {k.lower() for k in (comp_kw or []) if k and k.lower() not in GENERIC_KEYWORDS}
    return [a[k] for k in a if k in b]


def build(film: dict, comp: dict, categories: list[str], signals: dict | None = None,
          film_kw: list[str] | None = None, comp_kw: list[str] | None = None) -> dict:
    """The link between ``film`` and ``comp``: kinds found, who is shared, and MovieWeb demand for the classic's people."""
    signals = signals or {}
    kinds: list[str] = []
    detail: dict = {}
    if "franchise_sibling" in categories:
        kinds.append("franchise")
        detail["franchise"] = film.get("collection_name") or comp.get("collection_name")
    shared_dir = [d for d in _names(film.get("director")) if d in _names(comp.get("director"))]
    if shared_dir:
        kinds.append("director")
        detail["director"] = shared_dir
    shared_cast = [c for c in _names(film.get("lead_cast")) if c in _names(comp.get("lead_cast"))]
    if shared_cast:
        kinds.append("cast")
        detail["cast"] = shared_cast
    genres = film.get("genres") or []
    if "classic_same_genre" in categories and genres:
        kinds.append("genre")
        detail["genre"] = genres[0]
    themes = theme_keywords(film_kw, comp_kw)
    if len(themes) >= MIN_SHARED_KEYWORDS:
        kinds.append("theme")
        detail["theme"] = themes[:4]

    shared_people = set(shared_dir + shared_cast)
    demand = []
    for name in comp_people(comp):
        s = signals.get(name) or {}
        if s.get("is_signal"):
            demand.append({"name": name, "tier": s.get("tier"), "lift": s.get("lift"), "articles": s.get("articles"),
                           "shared": name in shared_people})
    demand.sort(key=lambda d: (d["tier"] != "high", -(d["lift"] or 0)))
    out = {"linked": bool(kinds), "kinds": kinds, "detail": detail, "demand": demand[:3]}
    out["text"] = text(out)
    return out


def text(link: dict) -> str:
    """One card sentence: the link in plain words, then the audience evidence."""
    d, bits = link.get("detail") or {}, []
    if "franchise" in link["kinds"]:
        bits.append(f"same franchise ({d['franchise']})" if d.get("franchise") else "same franchise")
    if "director" in link["kinds"]:
        bits.append("same director (" + ", ".join(d["director"]) + ")")
    if "cast" in link["kinds"]:
        bits.append("shares " + ", ".join(d["cast"]))
    if "genre" in link["kinds"]:
        bits.append(f"same main genre ({d['genre']})")
    if "theme" in link["kinds"]:
        bits.append("shared themes (" + ", ".join(d["theme"]) + ")")
    out = ("Link: " + "; ".join(bits) + ".") if bits else "Link: none found (notable film only)."
    if link.get("demand"):
        names = ", ".join(f"{x['name']} ({x['lift']:.1f}x MovieWeb News baseline, {x['articles']} articles)"
                          for x in link["demand"])
        out += f" MovieWeb demand for the classic's talent: {names}."
    return out


def strength_bonus(link: dict) -> float:
    b = LINK_STRENGTH if link.get("linked") else 0.0
    if link.get("demand"):
        b += max(DEMAND_STRENGTH.get(x.get("tier"), 0.0) for x in link["demand"])
    return b


def is_modern_vs_classic(cand: dict) -> bool:
    """The label test: a Chase or Overtake against a classic, with a real link between the two films."""
    return (cand.get("era") == "classic" and cand.get("strategy") in ("chase", "overtake")
            and bool((cand.get("link") or {}).get("linked")))


def enrich(events: list, films_by_id: dict, signals_fn: Callable[[list[str]], dict] | None = None,
           keywords_fn: Callable[[int], list[str]] | None = None) -> int:
    """Attach ``candidate["link"]`` (and the strength bonus) to every Chase/Overtake against a classic.
    Returns how many events were enriched. ``signals_fn(names)`` and ``keywords_fn(tmdb_id)`` are optional
    lookups (Cerebro snapshot, TMDB); without them the link rests on the corpus alone."""
    n = 0
    for e in events:
        c = e.candidate or {}
        comp = c.get("comp") or {}
        if e.strategy not in ("chase", "overtake") or comp.get("era") != "classic":
            continue
        film = films_by_id.get(e.film.get("tmdb_id")) or {"title": e.film.get("title")}
        cats = list(c.get("angle_categories") or [])
        signals = signals_fn(candidate_names(film, comp)) if signals_fn else {}
        film_kw = comp_kw = None
        if keywords_fn:
            try:
                film_kw, comp_kw = keywords_fn(film.get("tmdb_id")), keywords_fn(comp.get("tmdb_id"))
            except Exception:  # noqa: BLE001 — a failed lookup is no theme link, never an invented one
                film_kw = comp_kw = None
        link = build(film, comp, cats, signals, film_kw, comp_kw)
        c["link"] = link
        if not e.strength or e.strength >= 0.35:   # weak floor fillers keep their strength: they exist to fill the day
            e.strength = round(min(1.0, e.strength + strength_bonus(link)), 3)
        e.reason_codes = list(e.reason_codes) + (["link:" + k for k in link["kinds"]] or ["link:none"]) \
            + (["mw_demand"] if link["demand"] else [])
        n += 1
    return n
