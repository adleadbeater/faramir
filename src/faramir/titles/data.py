"""The MovieWeb evidence a title is written from (the Aragorn method, in Faramir).

* Power Words — read from the live Power Words Sheet (or its local snapshot), then **re-measured on
  the News + Flash News desk in Cerebro**: the sheet pools Niche News and runs 1.5–4× hot (Title
  Crafting Rules §2), so its own figure is a shortlist, never the number we act on.
* Subject and genre demand — exact-tag matches in the News + Flash News pool (matured articles):
  articles, sessions per article, failure rate, Discover clicks, and the top real headlines.

Both are rendered as the labelled blocks Aragorn's prompt expects.
"""

from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass

from faramir.analytics.cerebro_snapshot import Article, Snapshot

POWER_WORDS_SHEET_ID = "18CGvf8jl1i_vXcXDdinXnHNFSs60URRVSftaZ89eUi0"
POWER_WORDS_TAB = "Data Sheet ^^^"
POWER_WORDS_RANGE = "A4:F"          # A3 is the sheet's lookup cell; phrases start at row 4
DESK_TYPES = {"news", "flash news"}  # Title Crafting Rules §1: judge News titles on News + Flash only
FAIL_BELOW = 500
SMALL_SAMPLE = 5
# Title Crafting Rules §4 avoid list (and Aragorn's anti-patterns)
AVOID = {"begins production", "reveals why", "mistake", "mistakes", "wraps production", "final season", "teases",
         "villain", "video game adaptation", "horror", "after n seasons", "4k", "promising update", "admits",
         "exclusive", "divisive", "reveals", "revealed", "finally revealed", "unveils", "explains", "addresses",
         "almost", "nearly"}
# TMDB genre → MovieWeb genre tag
GENRE_TAGS = {"Science Fiction": "Sci-Fi", "Action": "Action", "Adventure": "Adventure", "Horror": "Horror",
              "Thriller": "Thriller", "Comedy": "Comedy", "Drama": "Drama", "Fantasy": "Fantasy",
              "Animation": "Animation", "Family": "Family", "Romance": "Romance", "Crime": "Crime",
              "Mystery": "Mystery", "War": "War", "Western": "Western", "History": "Historical",
              "Music": "Music", "Documentary": "Documentary"}


@dataclass
class Phrase:
    phrase: str
    sheet_uses: int
    sheet_sa: float
    desk_n: int = 0
    desk_sa: float | None = None
    desk_fr: float | None = None


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").replace("%", "").strip() or 0)
    except ValueError:
        return 0.0


def load_power_words(gc=None, snapshot_path: str | None = None) -> tuple[list[Phrase], str]:
    """(phrases, source). Live sheet when a Sheets client can open it; else the local snapshot."""
    if gc is not None:
        try:
            rows = gc.open_by_key(POWER_WORDS_SHEET_ID).worksheet(POWER_WORDS_TAB).get_values(POWER_WORDS_RANGE)
            out = [Phrase(r[0].strip(), int(_num(r[1])), _num(r[3])) for r in rows if r and r[0].strip() and len(r) >= 4]
            if out:
                return _dedupe(out), "power_words_sheet:live"
        except Exception:  # noqa: BLE001 — fall back to the snapshot; never fail a run over vocabulary
            pass
    if snapshot_path and os.path.exists(snapshot_path):
        out = []
        for line in open(snapshot_path, encoding="utf-8"):
            if line.startswith("#") or line.startswith("phrase\t") or not line.strip():
                continue
            p = line.rstrip("\n").split("\t")
            out.append(Phrase(p[0], int(_num(p[1])), _num(p[3])))
        return _dedupe(out), f"power_words_sheet:snapshot ({os.path.basename(snapshot_path)})"
    return [], "none"


def _dedupe(phrases: list[Phrase]) -> list[Phrase]:
    seen, out = set(), []
    for p in phrases:
        if p.phrase.lower() not in seen:
            seen.add(p.phrase.lower())
            out.append(p)
    return out


def desk_pool(snap: Snapshot, min_age_days: int = 14) -> list[Article]:
    return [a for a in snap.pool("news", min_age_days) if a.content_type.strip().lower() in DESK_TYPES]


def _stats(arts: list[Article]) -> dict:
    s = [a.sessions or 0 for a in arts]
    dc = [a.discover_clicks or 0 for a in arts]
    return {"articles": len(arts), "sa": round(statistics.fmean(s)) if s else 0,
            "fr": round(100 * sum(x < FAIL_BELOW for x in s) / len(s), 1) if s else None,
            "dca": round(statistics.fmean(dc)) if dc else 0, "best": round(max(s)) if s else 0}


def measure_phrases(phrases: list[Phrase], pool: list[Article]) -> list[Phrase]:
    for p in phrases:
        rx = re.compile(r"(?<![\w-])" + re.escape(p.phrase) + r"(?![\w])", re.I)
        hits = [a for a in pool if rx.search(a.title)]
        st = _stats(hits)
        p.desk_n, p.desk_sa, p.desk_fr = st["articles"], (st["sa"] if hits else None), st["fr"]
    return phrases


# Phrases tied to other story formats (streaming, reviews, TV, physical media): never offered for box office.
OTHER_FORMATS = re.compile(r"rotten|rt score|streaming|series|binge|netflix|episode|minute|-part|image|first-look|"
                           r"digital|season|download|free|quote|10/10|praise|confirms|announce", re.I)


def power_words_block(phrases: list[Phrase], baseline_sa: int, limit: int = 20, min_n: int = 3) -> str:
    usable = [p for p in phrases if p.desk_n >= min_n and p.phrase.lower() not in AVOID
              and not OTHER_FORMATS.search(p.phrase)]
    usable.sort(key=lambda p: -(p.desk_sa or 0))
    lines = ["--- PROVEN TITLE PHRASES (Power Words) ---",
             f"From MovieWeb's Power Words Sheet, re-measured on the News + Flash News desk (baseline S/A ≈ {baseline_sa}).",
             "The sheet's own figures run hot; these desk figures are the ones to trust. Use a phrase only if it",
             "accurately describes this story."]
    for p in usable[:limit]:
        flag = "" if p.desk_n >= SMALL_SAMPLE else " (small sample)"
        lines.append(f"- {p.phrase}: desk S/A ≈ {p.desk_sa:,.0f} · failure {p.desk_fr}% · {p.desk_n} uses{flag}")
    lines.append("Avoid: " + ", ".join(sorted(a.title() for a in AVOID if " n " not in a)))
    lines.append("--- END POWER WORDS ---")
    return "\n".join(lines)


# TMDB genre → the MovieWeb word for it
GENRE_WORD = {"Action": "Action", "Adventure": "Adventure", "Thriller": "Thriller", "Crime": "Crime",
              "Science Fiction": "Sci-Fi", "Horror": "Horror", "Mystery": "Mystery", "Drama": "Drama",
              "Comedy": "Comedy", "Fantasy": "Fantasy", "Romance": "Romance", "War": "War", "Western": "Western",
              "Animation": "Animated", "Family": "Family", "History": "Historical", "Music": "Musical",
              "Documentary": "Documentary"}
# Honest relabels (Title Crafting Rules §7 grey zone; Rob 2026-10-28: an Adventure + Thriller is an Action Thriller)
ADJACENT = {"Adventure": ["Action", "Epic"], "History": ["Epic"], "War": ["Epic"], "Fantasy": ["Epic"]}
# Second words that pair after a first genre word ("Crime Thriller", "Sci-Fi Action", "Action Comedy")
PAIR_TAILS = {"Thriller", "Action", "Comedy", "Drama", "Epic", "Horror", "Western", "Adventure"}


def genre_candidates(tmdb_genres: list[str]) -> list[str]:
    """Every genre wording that honestly describes a film with these TMDB genres."""
    words = []
    for g in tmdb_genres or []:
        if g in GENRE_WORD:
            words.append(GENRE_WORD[g])
        words += ADJACENT.get(g, [])
    words = list(dict.fromkeys(words))
    out = list(words)
    for first in words:
        for second in words:
            if first != second and second in PAIR_TAILS and first not in ("Epic",):
                out.append(f"{first} {second}")
    return list(dict.fromkeys(out))


SHRINK_K = 10   # same shrinkage strength as the ranker (priority_config shrinkage_k)


def genre_options(tmdb_genres: list[str], pool: list[Article], phrases: list[Phrase], min_n: int = SMALL_SAMPLE) -> list[dict]:
    """Accurate genre wordings ranked by MovieWeb performance: News + Flash desk S/A from Cerebro (titles
    containing the phrase); the Power Words Sheet's figure where the desk sample is too small. Ranked on
    S/A shrunk toward the desk baseline, so 16 strong uses don't outrank 291 nearly-as-strong ones."""
    base = statistics.fmean([a.sessions or 0 for a in pool]) if pool else 0
    sheet = {p.phrase.lower(): p for p in phrases}
    out = []
    for phrase in genre_candidates(tmdb_genres):
        rx = re.compile(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", re.I)
        st = _stats([a for a in pool if rx.search(a.title)])
        sp = sheet.get(phrase.lower())
        if st["articles"] >= min_n:
            out.append({"phrase": phrase, "sa": st["sa"], "n": st["articles"], "fr": st["fr"], "basis": "cerebro_desk"})
        elif sp and sp.sheet_uses >= min_n:
            out.append({"phrase": phrase, "sa": round(sp.sheet_sa), "n": sp.sheet_uses, "fr": None,
                        "basis": "power_words_sheet (runs hot)"})
    for o in out:
        o["rank_sa"] = round((o["n"] * o["sa"] + SHRINK_K * base) / (o["n"] + SHRINK_K))
    return sorted(out, key=lambda o: -o["rank_sa"])


# Verbs by what they say. A story only gets verbs that are true for it (a chase that hasn't happened never
# "Passes"), ranked by MovieWeb performance: News + Flash desk titles in Cerebro when the verb has
# MIN_VERB_N+ uses there, otherwise the Power Words Sheet discounted by how hot the sheet runs. Each
# entry: (verb as written in a title, regex that finds it, the word measured).
VERB_GROUPS = {
    "ahead": [("Eyes", r"eyes", "Eyes"), ("Is Set to Pass", r"(?:is )?set to", "Set to"),
              ("Closes In On", r"clos(?:es|ing) in on", "Closes In On"), ("Nears", r"near(?:s|ing)", "Nears"),
              ("Is on Track to Pass", r"(?:is )?on track to", "On Track"),
              ("Is About to Pass", r"(?:is )?about to", "About to")],
    "pass": [("Passes", r"passes", "Passes"), ("Beats", r"beats", "Beats"), ("Tops", r"tops", "Tops"),
             ("Overtakes", r"overtakes", "Overtakes"), ("Surpasses", r"surpasses", "Surpasses"),
             ("Topples", r"topples", "Topples"), ("Outshines", r"outshines", "Outshines"),
             ("Outgrosses", r"outgrosses", "Outgrosses")],
    "milestone": [("Hits", r"hits", "Hits"), ("Crosses", r"crosses", "Crosses"), ("Passes", r"passes", "Passes"),
                  ("Tops", r"tops", "Tops"), ("Surpasses", r"surpasses", "Surpasses")],
    "number_one": [("Hits No. 1", r"hits", "Hits"), ("Takes No. 1", r"takes", "Takes"), ("Leads", r"leads", "Leads"),
                   ("Tops", r"tops", "Tops"), ("Topples", r"topples", "Topples"), ("Dethrones", r"dethrones", "Dethrones"),
                   ("Dominates", r"dominates", "Dominates"), ("Conquers", r"conquers", "Conquers"),
                   ("Wins", r"wins", "Wins")],
    "hold": [("Remains No. 1", r"remains", "Remains"), ("Holds", r"holds", "Holds"), ("Stays", r"stays", "Stays"),
             ("Leads", r"leads", "Leads"), ("Dominates", r"dominates", "Dominates")],
}
MIN_VERB_N = 3
VERB_KEEP_WITHIN = 0.85    # a verb is allowed if its ranked S/A is within 15% of the best proven verb
MIN_ALLOWED = 2            # always leave the writer a choice of two


def verb_group(strategy: str, status: str, has_comp: bool) -> str | None:
    if strategy == "chase" or (status != "confirmed" and strategy in ("countdown", "overtake")):
        return "ahead"
    return {"takeover": "number_one", "hold": "hold", "dark_horse": "pass", "countdown": "milestone",
            "overtake": "pass" if has_comp else "milestone"}.get(strategy)


def sheet_heat(phrases: list[Phrase], min_n: int = SMALL_SAMPLE) -> float:
    """How hot the Power Words Sheet runs against the desk: median desk S/A ÷ sheet S/A over phrases measured
    in both (5+ desk uses). Used to discount a sheet figure when the desk has too few uses of a verb."""
    ratios = sorted(p.desk_sa / p.sheet_sa for p in phrases if p.desk_n >= min_n and p.desk_sa and p.sheet_sa)
    return ratios[len(ratios) // 2] if ratios else 0.5


def verb_options(group: str | None, pool: list[Article], phrases: list[Phrase] | None = None) -> list[dict]:
    """The group's verbs, ranked; "allowed" marks the ones a title may use (titles.check enforces it)."""
    if not group:
        return []
    base = statistics.fmean([a.sessions or 0 for a in pool]) if pool else 0
    sheet = {p.phrase.lower(): p for p in phrases or []}
    heat = sheet_heat(phrases or [])
    out = []
    for verb, rx, word in VERB_GROUPS[group]:
        st = _stats([a for a in pool if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w])", a.title, re.I)])
        sp = sheet.get(word.lower())
        if st["articles"] >= MIN_VERB_N:
            o = {"sa": st["sa"], "n": st["articles"], "basis": "cerebro_desk"}
        elif sp and sp.sheet_uses >= MIN_VERB_N:
            o = {"sa": round(sp.sheet_sa * heat), "n": sp.sheet_uses, "basis": f"power_words_sheet × {heat:.2f}"}
        else:
            o = {"sa": st["sa"], "n": st["articles"], "basis": "unproven"}
        o.update(phrase=verb, regex=rx, proven=o["basis"] != "unproven",
                 rank_sa=round((o["n"] * o["sa"] + SHRINK_K * base) / (o["n"] + SHRINK_K)) if o["basis"] != "unproven" else 0)
        out.append(o)
    out.sort(key=lambda o: (not o["proven"], -o["rank_sa"]))
    proven = [o for o in out if o["proven"]]
    for i, o in enumerate(out):
        o["allowed"] = o["proven"] and (i < MIN_ALLOWED or o["rank_sa"] >= VERB_KEEP_WITHIN * proven[0]["rank_sa"])
    return out


# Descriptors a title may only use when the data backs them (Rob, 2026-10-29: "Raunchy", "DCU's" and
# "Major Action Hit" were written with nothing behind them). Each: (label, title regex, test on one film's
# facts). Facts: genres, TMDB keywords and production companies, US rating, franchise, domestic/worldwide.
def _kw(*words):
    return lambda f: any(w in k for k in f["keywords"] for w in words)


def _co(*names):
    return lambda f: any(n.lower() in c.lower() for c in f["companies"] for n in names)


def _genre(*names):
    return lambda f: any(g in f["genres"] for g in names)


def _gross(dom, ww):
    return lambda f: (f.get("domestic") or 0) >= dom or (f.get("worldwide") or 0) >= ww


DESCRIPTORS = [
    ("Raunchy", r"raunchy", _kw("raunchy", "sex comedy", "gross-out")),
    ("Steamy", r"steamy|erotic|sexy", _kw("erotic", "steamy", "sensuality", "sex scene")),
    ("Gritty", r"gritty", _kw("gritty", "neo-noir", "grim")),
    ("Psychological", r"psychological", _kw("psychological")),
    ("Disturbing", r"disturbing|twisted", _kw("disturbing", "body horror", "twisted", "psychopath")),
    ("Brutal", r"brutal|bloody|gory|violent", _kw("gore", "violence", "brutal", "splatter", "decapitation", "bloody")),
    ("Terrifying", r"terrifying|scary|spine-chilling", _genre("Horror")),
    ("Hilarious", r"hilarious|funny", _genre("Comedy")),     # not TMDB's "hilarious" reaction keyword
    ("Heartwarming", r"heartwarming|feel-good", _kw("heartwarming", "feel-good", "feel good")),
    ("Mind-Bending", r"mind-bending", _kw("mind-bending", "surreal", "time loop", "nonlinear")),
    ("DCU", r"dcu(?:'s)?", lambda f: _kw("dc universe (dcu)")(f) or _co("DC Studios")(f)),
    ("DC", r"dc(?:'s)?", lambda f: _kw("dc comics", "dc universe", "dc extended universe")(f) or _co("DC Studios", "DC Comics", "DC Entertainment")(f)),
    ("MCU", r"mcu(?:'s)?", lambda f: _kw("marvel cinematic universe")(f) or _co("Marvel Studios")(f)),
    ("Marvel", r"marvel(?:'s)?", lambda f: _kw("marvel comics", "marvel cinematic universe")(f) or _co("Marvel")(f)),
    ("Pixar", r"pixar(?:'s)?", _co("Pixar")),
    ("Disney", r"disney(?:'s)?", _co("Disney")),
    ("Blumhouse", r"blumhouse(?:'s)?", _co("Blumhouse")),
    ("A24", r"a24(?:'s)?", _co("A24")),
    ("Superhero", r"superhero", _kw("superhero")),
    ("Comic Book", r"comic[- ]book", _kw("based on comic")),
    ("Video Game", r"video[- ]game", _kw("based on video game")),
    ("Zombie", r"zombie", _kw("zombie")),
    ("Martial Arts", r"martial[- ]arts", _kw("martial arts")),
    ("Sequel", r"sequel", lambda f: _kw("sequel")(f) or bool(f.get("collection"))),
    ("Reboot", r"reboot", _kw("reboot")),
    ("Remake", r"remake", _kw("remake")),
    ("R-Rated", r"r-rated", lambda f: f.get("certification") == "R"),
    ("Biopic", r"biopic", lambda f: _kw("biography", "biopic")(f) and "Documentary" not in f["genres"]),
    ("Hit", r"hit", _gross(100e6, 250e6)),
    ("Blockbuster", r"blockbuster|smash|major|juggernaut|megahit", _gross(200e6, 500e6)),
    ("Billion-Dollar", r"billion[- ]dollar", lambda f: (f.get("worldwide") or 0) >= 1e9),
    ("Flop", r"flop|bomb|disappointment", lambda f: False),    # no budget data: never supported
]


def film_facts(credits: dict, domestic=None, worldwide=None) -> dict:
    return {"genres": credits.get("genres") or [], "keywords": [k.lower() for k in credits.get("keywords") or []],
            "companies": credits.get("companies") or [], "certification": credits.get("certification") or "",
            "collection": credits.get("collection") or "", "domestic": domestic, "worldwide": worldwide}


def supported_descriptors(facts: list[dict]) -> list[str]:
    return [label for label, _, test in DESCRIPTORS if any(test(f) for f in facts)]


def unsupported_descriptors(title: str, facts: list[dict]) -> list[str]:
    """Descriptors in the title that no film in the story backs. Strip named films and people first."""
    ok = set(supported_descriptors(facts))
    return [m.group(0) for label, rx, _ in DESCRIPTORS if label not in ok
            for m in [re.search(r"(?<![\w-])(?:" + rx + r")(?![\w-])", title, re.I)] if m]


# How to describe a film (Title Crafting Rules §6 "mask weak tags, name strong ones"; Aragorn's draw hierarchy
# Star > high-draw Platform > Descriptor > Property). Every film in a story, the comparison included, gets the
# same treatment: a reader must be able to tell WHICH film it is. A category ("a Best Picture-Winning
# Thriller", "a Marvel Superhero Blockbuster") identifies nothing; a bare quoted title ('Street Fighter')
# is the weakest lead on the site. Anchors that identify a film: one of its people, its franchise, its gross.
PLATFORM_LABELS = ("DCU", "MCU", "Marvel", "DC", "Pixar", "Disney", "A24", "Blumhouse")


def _subject(pool: list[Article], name: str) -> dict:
    arts = [a for a in pool if _tagged(a, name)]
    return {"name": name, **_stats(arts)} if arts else {"name": name, "articles": 0, "sa": 0, "fr": None}


def _shrunk(e: dict, base: float) -> float:
    return (e["articles"] * e["sa"] + SHRINK_K * base) / (e["articles"] + SHRINK_K)


def _millions(x) -> str | None:
    if not x:
        return None
    return f"${x / 1e9:.1f} Billion" if x >= 1e9 else f"${round(x / 1e6):,} Million"


IDENTITY = ("Superhero", "Video Game", "Comic Book", "Zombie", "Martial Arts", "Biopic", "Reboot")
NOUNS = ("Thriller", "Comedy", "Drama", "Epic", "Western", "Documentary", "Musical", "Romance", "Movie", "Adventure")
PROVEN_PERSON = 3        # MovieWeb articles before a name counts as a draw
HIT_GROSS = 100e6        # below this, a gross isn't a selling point


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def film_kit(title: str, credits: dict, pool: list[Article], phrases: list[Phrase], gross=None,
             honour: str = "", facts: dict | None = None) -> dict:
    """Everything that can identify and sell one film in a title, with MovieWeb demand for each anchor."""
    base = statistics.fmean([a.sessions or 0 for a in pool]) if pool else 0
    cast = [p for p in (credits.get("cast") or [])[:5] if p]
    directors = [d.strip() for d in (credits.get("director") or "").split(",") if d.strip()]
    people = [_subject(pool, p) | {"role": "cast" if p in cast else "director"} for p in dict.fromkeys(cast + directors)]
    strong = lambda e: e["articles"] >= SMALL_SAMPLE and e["sa"] >= base  # noqa: E731
    proven = sorted((e for e in people if e["articles"] >= PROVEN_PERSON), key=lambda e: -_shrunk(e, base))
    # A star with real demand leads (Aragorn: STAR-LED is the strongest mechanism); otherwise the best proven name.
    lead_person = next((e for e in people if e["role"] == "cast" and strong(e)), None) or (proven[0] if proven else None)
    franchise = re.sub(r"\s*\((?:Film )?Collection\)$|\s+Collection$", "", credits.get("collection") or "").strip()
    supported = supported_descriptors([facts]) if facts else []
    labels = [e for e in (_subject(pool, x) for x in supported if x in PLATFORM_LABELS) if e["articles"]]
    ranked = genre_options(credits.get("genres") or [], pool, phrases)
    genre = ranked[0]["phrase"] if ranked else (genre_candidates(credits.get("genres") or []) or ["Movie"])[0]
    identity = next((x for x in IDENTITY if x in supported), "")
    core = " ".join(x for x in (honour, identity, genre) if x)
    if not core.endswith(NOUNS):
        core += " Movie"
    own = _subject(pool, title)
    return {"title": title, "title_demand": own, "name_title": strong(own), "people": people, "proven": proven,
            "lead_person": lead_person, "labels": labels, "lead_label": max(labels, key=lambda e: e["sa"], default=None),
            "franchise": franchise, "sequel": "Sequel" in supported, "core": core, "genre": genre,
            "gross": _millions(gross), "hit": bool(gross and gross >= HIT_GROSS), "honour": honour,
            "facts": facts or {}, "supported": supported}


def data_supported(facts: dict) -> list[str]:
    return supported_descriptors([facts])


def _demand(e: dict) -> str:
    return f"{e['articles']} MovieWeb articles, S/A ≈ {e['sa']:,}"


def kit_descriptions(k: dict) -> list[str]:
    """Ready-made strong descriptions, strongest mechanism first. Each one identifies the film."""
    out = []
    if k["lead_person"]:
        p = k["lead_person"]
        out.append(f"NAME-LED: \"{possessive(p['name'])} {k['core']}\" ({p['name']}: {_demand(p)})")
        alt = next((e for e in k["proven"] if e is not p), None)
        if alt:
            out.append(f"NAME-LED: \"{possessive(alt['name'])} {k['core']}\" ({alt['name']}: {_demand(alt)})")
    if k["franchise"]:
        kind = "Sequel" if k["sequel"] else k["core"]
        out.append(f"PLATFORM-WIDE: \"the {(k['gross'] + ' ') if k['hit'] else ''}{k['franchise']} {kind}\"")
    if k["lead_label"]:
        lab = k["lead_label"]
        out.append(f"PLATFORM-WIDE: \"{possessive(lab['name'])} {k['core']}\" ({lab['name']}: {_demand(lab)})")
    if k["hit"]:
        out.append(f"GENRE-HEAVY: \"{k['gross']} {k['core'].removesuffix(' Movie')}" + (" Hit" if "Hit" in k["supported"] else "") + "\"")
    top_billed = next((e for e in k["people"] if e["role"] == "cast"), None)
    if not k["lead_person"] and top_billed:
        out.append(f"NAME-LED: \"{possessive(top_billed['name'])} {k['core']}\" (top-billed; no MovieWeb track record)")
    return out


def kits_block(kits: list[dict], baseline: int) -> str:
    lines = ["--- HOW TO DESCRIBE EACH FILM (every film in the story, the comparison included) ---",
             "Describe every film as strongly as the lead: name-led (a star or director with MovieWeb demand), platform-wide",
             "(a franchise or studio label readers follow), or genre-heavy (gross + genre, the \"$540 Million Sci-Fi Action Hit\"",
             "that beat the named version 13,391 to 1,228). The reader must be able to tell WHICH film it is: never a bare",
             "category (\"a Best Picture-Winning Thriller\", \"a Marvel Superhero Blockbuster\") and never a bare quoted title",
             f"(quoting a title halves S/A). Name a film's title only if its own MovieWeb tag is strong (baseline ≈ {baseline:,})."]
    for k in kits:
        d = k["title_demand"]
        verdict = "strong: may be named" if k["name_title"] else "weak: describe it, don't name it"
        lines.append(f"{k['role']}: '{k['title']}' · own tag {d['articles']} articles, S/A ≈ {d['sa']:,} ({verdict})")
        lines += [f"  - {x}" for x in kit_descriptions(k)]
        shown = {x.split('"')[1].split("'")[0] for x in kit_descriptions(k)}
        others = [f"{e['name']} ({e['articles']}, S/A ≈ {e['sa']:,})" for e in k["people"] if e["articles"] and e["name"] not in shown]
        if others:
            lines.append("  - other people with MovieWeb demand: " + ", ".join(others))
    lines.append("--- END HOW TO DESCRIBE ---")
    return "\n".join(lines)


def anchors(k: dict) -> list[str]:
    """Words that identify this film in a title: its people's surnames, its franchise, its gross."""
    out = [e["name"] for e in k["people"]] + [e["name"].split()[-1] for e in k["people"] if " " in e["name"]]
    if k["franchise"]:
        out.append(k["franchise"])
    if k["gross"]:
        out.append(k["gross"])
    return [a for a in out if a]


def genre_block(film: str, options: list[dict], comp: str | None = None, comp_options: list[dict] | None = None) -> str:
    def rows(opts):
        return [f"  - {o['phrase']}: S/A ≈ {o['sa']:,} · {o['n']} uses · {o['basis']}" for o in opts[:6]]
    lines = ["--- GENRE WORDING (accurate options, ranked by MovieWeb performance) ---"]
    if options:
        lines += [f"{film}:"] + rows(options)
    if comp and comp_options:
        lines += [f"'{comp}' (if you describe it instead of naming it):"] + rows(comp_options)
    lines.append("--- END GENRE WORDING ---")
    return "\n".join(lines) if len(lines) > 2 else ""


def _tagged(a: Article, tag: str) -> bool:
    t = tag.strip().lower()
    return a.pri_tag.strip().lower() == t or any(x.strip().lower() == t for x in a.tags)


def demand(pool: list[Article], subjects: list[str], genres: list[str], top_titles: int = 5) -> dict:
    def entry(name):
        arts = [a for a in pool if _tagged(a, name)]
        if not arts:
            return None
        top = sorted(arts, key=lambda a: -(a.sessions or 0))[:top_titles]
        return {"subject": name, **_stats(arts), "small_sample": len(arts) < SMALL_SAMPLE,
                "top": [(round(a.sessions or 0), round(a.discover_clicks or 0), a.title) for a in top]}
    subj = [e for e in (entry(s) for s in dict.fromkeys(x for x in subjects if x)) if e]
    gen = [e for e in (entry(g) for g in dict.fromkeys(x for x in genres if x)) if e]
    missing = [s for s in dict.fromkeys(x for x in subjects if x) if s not in {e["subject"] for e in subj}]
    return {"subjects": subj, "genres": gen, "no_coverage": missing}


def demand_block(d: dict, baseline_sa: int) -> str:
    if not d["subjects"] and not d["genres"]:
        return ""
    lines = ["--- REAL MOVIEWEB PERFORMANCE DATA (Cerebro) ---",
             f"MovieWeb News + Flash News baseline: Sess/Art ≈ {baseline_sa}.",
             "These are ACTUAL MovieWeb numbers and best-performing past headlines (exact tag matches, matured",
             "articles). Weigh GENRE DEMAND (how this kind of story lands) and SUBJECT DEMAND (how this specific",
             "film, person or franchise lands) separately."]
    for heading, entries, noun in (("## GENRE DEMAND (how this KIND of story performs)", d["genres"], "GENRE"),
                                   ("## SUBJECT DEMAND (how this specific subject performs)", d["subjects"], "SUBJECT")):
        if not entries:
            continue
        lines += ["", heading]
        for m in entries:
            small = " · SMALL SAMPLE" if m["small_sample"] else ""
            lines.append(f'{noun} "{m["subject"]}" — {m["articles"]} articles · Sess/Art ≈ {m["sa"]:,} · failure {m["fr"]}% '
                         f'· {m["dca"]:,} Discover clicks/article · best {m["best"]:,}{small}.')
            lines.append(f"  Top-performing real MovieWeb headlines for this {noun.lower()}:")
            lines += [f"    - [{s:,} sess / {dc:,} Discover] {t}" for s, dc, t in m["top"]]
    if d["no_coverage"]:
        lines += ["", "No MovieWeb News coverage (no proven demand): " + ", ".join(d["no_coverage"])]
    lines.append("--- END MOVIEWEB DATA ---")
    return "\n".join(lines)
