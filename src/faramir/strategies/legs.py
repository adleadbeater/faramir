"""Legs (strategy 7): unusual staying power for the release week.

Fires when F's weekend-over-weekend change is better than the cohort's 10th-percentile decline
(i.e. in the top 10% of holds) or its cumulative-to-opening multiplier exceeds the cohort's 90th
percentile for that release week [CAL]. Cohort = genre × opening-theater band × week number,
built from committed history; fewer than 20 films [CAL] → no event. Guards: theaters may not have
grown more than 10% (that is an expansion), and holiday weekends are not compared with ordinary
ones. Can fire while F is falling in rank.
"""

from __future__ import annotations

from datetime import date, timedelta

from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, film_ref, money
from faramir.strategies.weekend import Weekend

MIN_COHORT = 20
MAX_THEATER_GROWTH = 0.10
PCTL = 0.9


def _band(theaters) -> str:
    t = theaters or 0
    return "wide" if t >= 2000 else "mid" if t >= 600 else "limited"


def week_number(days_in_release) -> int:
    return ((days_in_release or 1) - 1) // 7 + 1


def holiday_weekends(year: int) -> set[str]:
    """Fridays of US holiday weekends [CAL]: Memorial Day, July 4th, Labor Day, Thanksgiving, Christmas, New Year."""
    def nth_weekday(month, weekday, n):
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    def last_weekday(month, weekday):
        d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    def friday_of(d):
        return (d - timedelta(days=(d.weekday() - 4) % 7)).isoformat()

    memorial = last_weekday(5, 0)
    labor = nth_weekday(9, 0, 1)
    thanksgiving = nth_weekday(11, 3, 4)
    return {friday_of(memorial - timedelta(days=3)), friday_of(date(year, 7, 4)), friday_of(labor - timedelta(days=3)),
            (thanksgiving + timedelta(days=1)).isoformat(), friday_of(date(year, 12, 25)), friday_of(date(year, 12, 31))}


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def detect(series: list[Weekend | None], history: list[Weekend] | None = None, genres: dict | None = None) -> DetectorResult:
    """series: the recent consecutive weekends (last = this weekend); history: every committed weekend
    available for cohorts; genres: {tmdb_id: [genre, …]} from TMDB (unknown genre → 'unknown')."""
    out = DetectorResult()
    if len(series) < 2 or series[-1] is None or series[-2] is None:
        return out
    cur, prev = series[-1], series[-2]
    if cur.weekend_start in holiday_weekends(int(cur.weekend_start[:4])) | holiday_weekends(int(cur.weekend_start[:4]) - 1):
        out.rejected.append({"strategy": "legs", "film_key": None, "film": None, "axis": "weekend", "target": "retention",
                             "reason_code": "holiday_weekend", "detail": f"{cur.weekend_start} is a holiday weekend"})
        return out
    genres = genres or {}
    hist = sorted([w for w in (history or []) if w is not None] + [w for w in series if w is not None],
                  key=lambda w: w.weekend_start)
    by_week = {w.weekend_start: w for w in hist}
    holidays = set().union(*(holiday_weekends(y) for y in {int(w.weekend_start[:4]) for w in hist}))

    def genre_of(row):
        g = genres.get(row.tmdb_id) if row.tmdb_id else None
        return (g[0] if g else "unknown")

    opening: dict[str, tuple] = {}
    for w in hist:
        for r in w.rows:
            if r.prev_status == "new" and r.film_key not in opening and r.gross:
                opening[r.film_key] = (r.gross, r.theaters)
    cohort_change: dict[tuple, list[float]] = {}
    cohort_mult: dict[tuple, list[float]] = {}
    for w in hist:
        if w.weekend_start in holidays:
            continue
        p = by_week.get((date.fromisoformat(w.weekend_start) - timedelta(days=7)).isoformat())
        if p is None:
            continue
        pb = p.by_key()
        for r in w.rows:
            q = pb.get(r.film_key)
            if not (q and q.gross and r.gross is not None and r.film_key in opening):
                continue
            key = (genre_of(r), _band(opening[r.film_key][1]), week_number(r.days_in_release))
            cohort_change.setdefault(key, []).append((r.gross - q.gross) / q.gross)
            if r.total_gross:
                cohort_mult.setdefault(key, []).append(r.total_gross / opening[r.film_key][0])
    pb = prev.by_key()
    for r in cur.rows:
        q = pb.get(r.film_key)
        if not (q and q.gross and r.gross is not None and r.film_key in opening):
            continue
        target = {"type": "retention", "value": week_number(r.days_in_release)}
        if q.theaters and r.theaters and (r.theaters - q.theaters) / q.theaters > MAX_THEATER_GROWTH:
            out.reject("legs", r.film(), "weekend", target, "expansion_driven", f"theaters {q.theaters} → {r.theaters}")
            continue
        key = (genre_of(r), _band(opening[r.film_key][1]), week_number(r.days_in_release))
        change = (r.gross - q.gross) / q.gross
        mult = r.total_gross / opening[r.film_key][0] if r.total_gross else None
        changes = [c for c in cohort_change.get(key, [])]
        mults = [m for m in cohort_mult.get(key, [])]
        if len(changes) < MIN_COHORT:
            out.reject("legs", r.film(), "weekend", target, "cohort_too_small", f"{len(changes)} films in cohort {key}")
            continue
        hold_cut, mult_cut = _quantile(changes, PCTL), _quantile(mults, PCTL) if mults else None
        why = []
        if change > hold_cut:
            why.append(f"weekend change {change:+.0%} vs cohort 90th percentile {hold_cut:+.0%}")
        if mult is not None and mult_cut is not None and mult > mult_cut:
            why.append(f"multiplier {mult:.2f}× vs cohort 90th percentile {mult_cut:.2f}×")
        if not why:
            continue
        out.emit(Event(
            strategy="legs", film=film_ref(r.film()), period=cur.weekend_start, axis="weekend", target=target,
            status="confirmed",
            evidence={"weekend_change_pct": round(100 * change, 1), "multiplier": round(mult, 2) if mult else None,
                      "cohort": list(key), "cohort_n": len(changes), "hold_cutoff_pct": round(100 * hold_cut, 1),
                      "multiplier_cutoff": round(mult_cut, 2) if mult_cut else None, "weekend_gross": r.gross,
                      "previous_weekend_gross": q.gross, "rank": r.source_rank},
            sources=[cur.source(), prev.source()],
            reason=f"{r.title} shows unusual legs in week {key[2]}: " + "; ".join(why) + f" (cohort n = {len(changes)}).",
            reason_codes=["top_decile_hold"] if change > hold_cut else ["top_decile_multiplier"],
            strength=0.65))
    return out
