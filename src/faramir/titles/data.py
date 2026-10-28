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
