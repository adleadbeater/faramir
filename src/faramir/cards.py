"""Editor cards: Faramir's ranked stories, each with its numbers, context, sources and an Aragorn title.

One module for production (scripts/run_daily.py posts the cards to Slack) and the local harness
(scripts/faramir_run.py writes them to out/cards/). Data sources are passed in, never read from a fixed
place: the Cerebro snapshot, the Power Words phrases and the TMDB credits cache.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date

from faramir import config, links
from faramir.strategies.common import money
from faramir.tn_chart import daily_url, weekend_url

LABEL = {"overtake": "Overtake", "chase": "Chase", "countdown": "Countdown", "takeover": "Takeover", "hold": "Hold",
         "streak": "Streak", "legs": "Legs", "exit": "Exit", "dark_horse": "Dark Horse", "discovery": "Discovery",
         "star_record": "Star Record", "global_split": "Global Split", "studio_battle": "Studio Battle",
         "premium_run": "Premium Run"}
CLASSIC_LABEL = "Modern vs. Classic"   # a Chase or Overtake against a film 25+ years old, compared in today's dollars

def _context(rationale: str) -> str:
    parts = [p.strip() for p in (rationale or "").split(" · ")]
    body = [p for p in parts[1:] if not p.startswith("sources:") and not p.startswith("also ")]
    return " · ".join(body)


def _numbers(film: dict, c: dict) -> list[str]:
    lines = []
    if c["kind"] != "event":
        lines.append(f"{film['title']}: {money(film['domestic_today'])} domestic · "
                     f"{money(film['worldwide_today']) if film.get('worldwide_today') else 'worldwide n/a'} worldwide")
        comp = c.get("comp")
        if comp:
            dom_l, ww_l = comp.get("domestic_lifetime"), comp.get("worldwide_lifetime")
            adj = comp.get("adjusted")
            if adj:   # a classic: both the raw and the restated figures, and exactly how (faramir/inflation.py)
                dom_n, ww_n = comp.get("domestic_nominal"), comp.get("worldwide_nominal")
                lines.append(f"{comp['title']} ({comp.get('release_year')}): "
                             f"{money(dom_n) if dom_n else 'n/a'} domestic · {money(ww_n) if ww_n else 'n/a'} worldwide at release"
                             f" = about {money(dom_l) if dom_l else 'n/a'} domestic · {money(ww_l) if ww_l else 'n/a'} worldwide "
                             f"in {adj['year_now']} dollars (lifetime; compared in {adj['year_now']} dollars)")
            else:
                lines.append(f"{comp['title']} ({comp.get('release_year')}): "
                             f"{money(dom_l) if dom_l else 'n/a'} domestic · {money(ww_l) if ww_l else 'n/a'} worldwide (lifetime)")
        elif c.get("threshold_value"):
            lines.append(f"Milestone: {money(c['threshold_value'])} {c['axis']}")
        return lines
    v = c.get("event_values") or {}
    keys = [("weekend_gross", "weekend gross"), ("previous_weekend_gross", "previous weekend"), ("rank", "rank"),
            ("theaters", "theaters"),
            ("previous_leader", "previous No. 1"), ("rival", "rival"), ("rival_weekend_gross", "rival weekend gross"),
            ("rival_theaters", "rival theaters"), ("previous_theaters", "previous theaters"), ("drop_pct", "theater drop %"),
            ("total_gross", "total"), ("weekends", "consecutive weekends"), ("weekend_change_pct", "weekend change %"),
            ("theater_avg", "per-theater average")]
    shown = []
    for k, label in keys:
        if v.get(k) is not None:
            val = v[k]
            if k in ("weekend_gross", "previous_weekend_gross", "rival_weekend_gross", "total_gross"):
                val = money(val)
            elif k == "theater_avg":
                val = f"${val:,.0f}"
            elif isinstance(val, (int, float)) and k in ("theaters", "rival_theaters", "previous_theaters"):
                val = f"{val:,.0f}"
            shown.append(f"{label} {val}")
    lines.append(f"{film['title']}: " + " · ".join(shown))
    return lines


def _sources(film: dict, c: dict, data_date: str, weekend: str, imdb: dict) -> list[str]:
    if c["kind"] == "event":
        return [weekend_url(date.fromisoformat(weekend))]
    out = [daily_url(date.fromisoformat(data_date))]
    if c["axis"] == "worldwide" and imdb.get(film["tmdb_id"]):
        out.append(f"https://www.boxofficemojo.com/title/{imdb[film['tmdb_id']]}/")
    return out


def build(payload: dict | None, report: dict, imdb: dict, limit: int) -> list[dict]:
    """Today's cards: Faramir's ranked candidates, one per film × strategy, at most two per film."""
    data_date = (report.get("source_dates") or {}).get("daily_chart") or report.get("run_date")
    weekend = (report.get("weekend_strategies") or {}).get("weekend_start")
    cards = []
    if not payload:
        return cards
    ranked = sorted(((f, c) for f in payload["active_films"] for c in f["candidates"] if c.get("rank")),
                    key=lambda fc: (bool(fc[1].get("floor_fill")), fc[1]["rank"]))   # floor fillers after every real story
    per_film: dict = {}
    seen = set()
    for film, c in ranked:
        key = (film["tmdb_id"], c.get("strategy"))
        if key in seen or per_film.get(film["tmdb_id"], 0) >= 2:
            continue   # one card per film × strategy, at most two per film (the writer's cap)
        seen.add(key)
        per_film[film["tmdb_id"]] = per_film.get(film["tmdb_id"], 0) + 1
        label = LABEL.get(c.get("strategy"), c.get("strategy"))
        if links.is_modern_vs_classic(c):
            label = CLASSIC_LABEL
        elif c.get("secondary_strategy"):
            label += f" (+{LABEL.get(c['secondary_strategy'], c['secondary_strategy'])})"
        cards.append({"rank": c["rank"], "label": label, "title": None, "numbers": _numbers(film, c),
                      "context": _context(c.get("rationale")), "sources": _sources(film, c, data_date, weekend, imdb),
                      "exploration": bool(c.get("exploration")), "floor_fill": bool(c.get("floor_fill")),
                      "_film": film, "_cand": c})
        if len(cards) >= limit:
            break
    return cards


def _first_reason(logic: str) -> str:
    """The kill reasoning's first substantive sentence, for a one-line card note."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", logic or "") if x.strip()]
    sentences = [x for x in sentences if not x.lower().startswith("all three kill")] or sentences
    return sentences[0] if sentences else "too soft to run"



def skip_reason(card: dict, cards: list[dict], stop_after: int | None) -> str | None:
    """Why this card needs no title yet: the day's slots are full, or it is a floor filler (a weak story promoted
    only so at least ``config.DAILY_CARD_FLOOR`` cards go out) and the day already has that many."""
    if stop_after is None:
        return None
    have = sum(postable(x) for x in cards)
    if have >= stop_after:
        return "not titled: today's slots are full"
    if card.get("floor_fill") and have >= config.DAILY_CARD_FLOOR:
        return "not titled: the day's floor is already met by stronger stories"
    return None


def write_titles(cards: list[dict], *, snap, phrases: list, pw_source: str, corpus_films: list[dict], credits,
                 data_date: str | None, weekend: str | None, client=None, briefs_dir: str | None = None,
                 only: set | None = None, previous: dict | None = None, stop_after: int | None = None,
                 on_usage=None) -> str:
    """Build each card's title brief and, with a Claude ``client``, write its title with the Aragorn method.
    ``snap`` is the Cerebro snapshot (None: no titles); ``previous`` keeps titles already written today
    (keyed by label and numbers) so a rerun never pays twice for the same card. With ``stop_after``, no
    more titles are written once that many cards can be posted (the day's slots are full)."""
    from faramir.titles import aragorn, brief as brief_mod, data as tdata

    if not cards:
        return ""
    if snap is None:
        for c in cards:
            c["title_note"] = "no title: Cerebro data unavailable"
        return "titles: Cerebro data unavailable"
    pool = tdata.desk_pool(snap)
    baseline = round(sum(a.sessions or 0 for a in pool) / len(pool)) if pool else 0
    tdata.measure_phrases(phrases, pool)
    others = sorted({f["title"] for f in corpus_films if " " in f["title"]}
                    | {c["_film"]["title"] for c in cards}, key=len, reverse=True)
    if briefs_dir:
        shutil.rmtree(briefs_dir, ignore_errors=True)   # briefs are rebuilt every run; never leave stale ones
        os.makedirs(briefs_dir, exist_ok=True)
    tokens = {"input_tokens": 0, "output_tokens": 0}
    year = int((data_date or str(date.today()))[:4])
    for i, c in enumerate(cards, 1):
        skip = skip_reason(c, cards, stop_after)
        if skip:
            c["title_note"] = skip
            continue
        film, cand = c["_film"], c["_cand"]
        comp_id = (cand.get("comp") or {}).get("tmdb_id")
        v = cand.get("event_values") or {}
        other = {v[k]: credits.by_title(v[k], year) for k in ("previous_leader", "rival") if v.get(k)}
        b = brief_mod.build(film, cand, credits.by_id(film.get("tmdb_id")), data_date, weekend, c["sources"], pool,
                            baseline, phrases, credits.by_id(comp_id) if comp_id else None, other)
        if briefs_dir:
            with open(os.path.join(briefs_dir, f"{i:02d}_{cand.get('strategy')}.txt"), "w") as f:
                f.write(b.user_message())
        prior = (previous or {}).get((c["label"], tuple(c["numbers"])))
        already = bool(prior and prior.get("title_verdict") and prior.get("title_verdict") != "error")
        if client is None or (only and i not in only) or (not only and already):
            if prior and prior.get("title_verdict"):
                c.update({k: prior[k] for k in ("title", "alternative", "title_verdict", "title_logic",
                                                "title_options", "title_note") if k in prior})
            else:
                c["title_note"] = "brief ready (run with --titles to write it)"
            continue
        t = aragorn.write(b, client, [o for o in others if o not in b.names])
        c.update({"title": t.title, "alternative": t.alternative, "title_verdict": t.verdict,
                  "title_logic": t.note, "title_options": t.options})
        if t.verdict == "kill":
            c["title_note"] = "Pass: " + _first_reason(t.note)
        elif t.verdict == "over_length":
            c["title_note"] = t.note
        elif not t.title:
            c["title_note"] = f"no title passed the fact check ({t.verdict})"
        for k in tokens:
            tokens[k] += t.usage.get(k, 0)
        if on_usage is not None:
            on_usage(t.usage)
    cost = tokens["input_tokens"] * 3 / 1e6 + tokens["output_tokens"] * 15 / 1e6
    return (f"titles: {pw_source}; Cerebro News+Flash baseline S/A {baseline:,} ({len(pool):,} articles, "
            f"as of {snap.as_of})" + (f"; briefs in {briefs_dir}" if briefs_dir else "")
            + (f"; {tokens['input_tokens']:,} in / {tokens['output_tokens']:,} out tokens ≈ ${cost:.2f}" if client else ""))


def select(payload: dict | None, *, gc, data_date: str, weekend: str | None, imdb: dict, corpus_films: list[dict],
           snap, slots: int, client, credits, extra: int = 2, on_usage=None) -> tuple[list[dict], str]:
    """Production: today's ranked cards, titled until ``slots`` of them can be posted (``extra`` spare cards
    stand in for any the title stage passes on). Power Words come from the live sheet when the service
    account can open it, else FARAMIR_POWER_WORDS_SNAPSHOT, else none (titles still use Cerebro)."""
    from faramir.titles import data as tdata

    report = {"source_dates": {"daily_chart": data_date}, "weekend_strategies": {"weekend_start": weekend}}
    cards = build(payload, report, imdb, slots + extra)
    phrases, pw_source = tdata.load_power_words(gc, os.environ.get("FARAMIR_POWER_WORDS_SNAPSHOT"))
    note = write_titles(cards, snap=snap, phrases=phrases, pw_source=pw_source, corpus_films=corpus_films,
                        credits=credits, data_date=data_date, weekend=weekend, client=client, stop_after=slots,
                        on_usage=on_usage)
    return cards, note


def postable(card: dict) -> bool:
    """A card goes to Slack only with a title that passed the fact check (never a kill, never untitled)."""
    return bool(card.get("title")) and card.get("title_verdict") not in ("kill", "error", "over_length")


def to_pick(card: dict) -> dict:
    """The card as a pick for faramir.binding: it binds to exactly the candidate it was built from."""
    film, cand = card["_film"], card["_cand"]
    comp = cand.get("comp") or {}
    category = cand["strategy"] if cand.get("kind") == "event" else (list(cand.get("angle_categories") or [""])[0])
    return {"active_tmdb_id": film.get("tmdb_id"), "active_title": film.get("title", ""),
            "comp_tmdb_id": comp.get("tmdb_id"), "comp_title": comp.get("title", ""),
            "axis": cand.get("axis"), "kind": cand.get("kind"), "threshold_value": cand.get("threshold_value"),
            "angle_category": category, "headline": card["title"], "angle": card.get("context", ""),
            "alternative": card.get("alternative") or "", "strategy": cand.get("strategy"),
            "numbers": list(card["numbers"]), "sources": list(card["sources"]), "card_label": card["label"]}


def render(cards: list[dict], run_date: date, report: dict) -> str:
    data_date = (report.get("source_dates") or {}).get("daily_chart")
    out = [f"# Faramir — {run_date:%A, %B %-d, %Y}", f"Daily chart {data_date} · weekend "
           f"{(report.get('weekend_strategies') or {}).get('weekend_start')}", ""]
    if not cards:
        out.append("_No story candidates today._")
    for i, c in enumerate(cards, 1):
        out.append(f"**{i}. {c['label'].upper()}**" + ("  _(test slot)_" if c.get("exploration") else ""))
        if c["title"]:
            out.append(f"**{c['title']}**" + (f"  _({c['title_note']})_" if c.get("title_verdict") == "over_length" else ""))
            if c.get("alternative"):
                out.append(f"_Alternative: {c['alternative']}_")
        else:
            out.append(f"_Title: {c.get('title_note') or 'not written (run with --titles)'}_")
        out += [f"- {n}" for n in c["numbers"]]
        if c["context"]:
            out.append(c["context"])
        out.append("Sources: " + " · ".join(c["sources"]))
        out.append("")
    return "\n".join(out)
