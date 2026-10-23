"""Dark Horse (strategy 13): a smaller film out-grossing a bigger one at the same stage.

Fires when, at a comparable release stage (±1 week), F out-grosses a film G with a verified scale
advantage: at least 1.5× the theaters [CAL], or a franchise film against F as an original (TMDB
collection), or — only if verified budgets exist — a budget at least 3× larger [CAL]. F must rank
in the top 5. Never claims marketing spend ("heavily promoted"): there is no such data.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref, money
from faramir.strategies.legs import week_number
from faramir.strategies.weekend import Weekend

THEATER_RATIO = 1.5
BUDGET_RATIO = 3.0


def detect(series: list[Weekend | None], metadata: dict | None = None) -> DetectorResult:
    """metadata: {tmdb_id: {"collection_id": …, "budget": verified int | None}} (TMDB)."""
    out = DetectorResult()
    cur = series[-1] if series else None
    if cur is None:
        return out
    metadata = metadata or {}
    for f in cur.rows:
        if not f.source_rank or f.source_rank > 5 or not f.gross:
            continue
        fm = metadata.get(f.tmdb_id, {}) if f.tmdb_id else {}
        best = None
        for g in cur.rows:
            if g.film_key == f.film_key or not g.gross or g.gross >= f.gross:
                continue
            if abs(week_number(g.days_in_release) - week_number(f.days_in_release)) > 1:
                continue
            gm = metadata.get(g.tmdb_id, {}) if g.tmdb_id else {}
            reasons = []
            if f.theaters and g.theaters and g.theaters >= THEATER_RATIO * f.theaters:
                reasons.append(f"{g.theaters:,} vs {f.theaters:,} theaters")
            if gm.get("collection_id") and not fm.get("collection_id") and f.tmdb_id and g.tmdb_id:
                reasons.append("a franchise film against an original")
            if fm.get("budget") and gm.get("budget") and gm["budget"] >= BUDGET_RATIO * fm["budget"]:
                reasons.append(f"verified budget {money(gm['budget'])} vs {money(fm['budget'])}")
            if reasons and (best is None or g.gross > best[0].gross):
                best = (g, reasons)
        if best is None:
            continue
        g, reasons = best
        out.emit(Event(
            strategy="dark_horse", film=film_ref(f.film()), period=cur.weekend_start, axis="weekend",
            target={"type": "film", "id": g.film_key, "title": g.title}, status="confirmed",
            evidence={"weekend_gross": f.gross, "rival_weekend_gross": g.gross, "theaters": f.theaters,
                      "rival_theaters": g.theaters, "rank": f.source_rank, "rival": g.title, "scale_advantage": reasons,
                      "week_number": week_number(f.days_in_release), "rival_week_number": week_number(g.days_in_release)},
            sources=[cur.source()],
            reason=(f"{f.title} ({money(f.gross)}, No. {f.source_rank}) out-grossed {g.title} ({money(g.gross)}) at the same "
                    f"stage despite {g.title}'s scale advantage: " + "; ".join(reasons) + "."),
            reason_codes=["outgrossed_bigger_film"] + [f"advantage:{x.split()[0]}" for x in reasons], strength=0.7))
    return out
