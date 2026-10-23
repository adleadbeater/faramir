"""Deterministic Discovery generators over committed weekend history (plan §4).

Each generator returns DiscoveryItems whose every number is a verifiable claim. Thresholds are
[CAL] starting values. Generators that need data Faramir doesn't have (territory rollout) are
not implemented; format contrast works only where TMDB metadata identifies the format.
"""

from __future__ import annotations

import statistics

from faramir.discovery.items import Claim, DiscoveryItem
from faramir.strategies.common import money
from faramir.strategies.weekend import Weekend

TOP_N = 10
COMEBACK_ABSENCE_WEEKS = 2
RERELEASE_SHARE_PCT = 10.0
PTA_MULTIPLE = 3.0
PTA_MIN_THEATERS = 50
Z_THRESHOLD = 2.0
MIN_COHORT = 8
EXPANSION_PCT = 50.0


def _claim(w: Weekend, row, metric: str, value, tol: float = 0.0) -> Claim:
    return Claim(row.film_key if row else None, metric, float(value), w.weekend_start,
                 f"{w.collection_id}:{row.film_key if row else 'chart'}", tol)


def comeback(weeks: list[Weekend]) -> list[DiscoveryItem]:
    """A film back in the top 10 after ≥ 2 complete weekends outside it (weeks: oldest → newest, no gaps)."""
    if len(weeks) < COMEBACK_ABSENCE_WEEKS + 2:
        return []
    cur, absent, before = weeks[-1], weeks[-1 - COMEBACK_ABSENCE_WEEKS:-1], weeks[:-1 - COMEBACK_ABSENCE_WEEKS]
    out = []
    for r in cur.rows:
        if not r.source_rank or r.source_rank > TOP_N:
            continue
        was_top = any((w.by_key().get(r.film_key) and (w.by_key()[r.film_key].source_rank or 99) <= TOP_N) for w in before)
        gone = all(not (w.by_key().get(r.film_key) and (w.by_key()[r.film_key].source_rank or 99) <= TOP_N) for w in absent)
        if was_top and gone:
            out.append(DiscoveryItem(
                "comeback", cur.weekend_start, r.film_key, r.title, "source_rank", r.source_rank,
                {"absent_weekends": [w.weekend_start for w in absent]},
                [_claim(cur, r, "source_rank", r.source_rank)] +
                [_claim(w, w.by_key()[r.film_key], "source_rank", w.by_key()[r.film_key].source_rank or 0)
                 for w in absent if w.by_key().get(r.film_key) and w.by_key()[r.film_key].source_rank],
                f"{r.title} is back at No. {r.source_rank} after {COMEBACK_ABSENCE_WEEKS} weekends outside the top {TOP_N}.",
                f"{r.title} Returns to the Box Office Top {TOP_N}", f"{r.title} Climbs Back Into the Top {TOP_N}",
                ["comeback", "rank_reentry"]))
    return out


def rerelease_share(w: Weekend) -> list[DiscoveryItem]:
    total = sum(r.gross or 0 for r in w.rows)
    rr = [r for r in w.rows if r.engagement == "rerelease"]
    share = 100 * sum(r.gross or 0 for r in rr) / total if total else 0
    if not rr or share < RERELEASE_SHARE_PCT:
        return []
    top = max(rr, key=lambda r: r.gross or 0)
    return [DiscoveryItem(
        "rerelease_share", w.weekend_start, None, None, "rerelease_share_pct", round(share, 1),
        {"threshold_pct": RERELEASE_SHARE_PCT, "rereleases": len(rr)},
        [_claim(w, None, "rerelease_share_pct", round(share, 1), 0.05)] + [_claim(w, r, "weekend_gross", r.gross) for r in rr],
        f"Re-releases took {share:.1f}% of the weekend's chart gross ({len(rr)} titles; largest {top.title}, {money(top.gross)}).",
        f"Re-Releases Took {share:.0f}% of the Weekend Box Office", f"{top.title} Leads a Big Re-Release Weekend",
        ["rerelease", "catalog_demand"])]


def per_theater_outlier(w: Weekend) -> list[DiscoveryItem]:
    rows = [r for r in w.rows if r.gross and r.theaters and r.theaters >= PTA_MIN_THEATERS]
    if len(rows) < MIN_COHORT:
        return []
    med = statistics.median(r.gross / r.theaters for r in rows)
    out = []
    for r in rows:
        pta = r.gross / r.theaters
        if pta >= PTA_MULTIPLE * med and (r.source_rank or 99) > 3:
            out.append(DiscoveryItem(
                "per_theater_outlier", w.weekend_start, r.film_key, r.title, "theater_avg", round(pta),
                {"chart_median_pta": round(med), "multiple": round(pta / med, 1)},
                [_claim(w, r, "weekend_gross", r.gross), _claim(w, r, "theaters", r.theaters),
                 _claim(w, r, "theater_avg", round(pta), 0.5)],
                f"{r.title} averaged {money(pta) if pta >= 1e6 else f'${pta:,.0f}'} per theater in {r.theaters:,} theaters, "
                f"{pta / med:.1f}× the chart median, while ranked outside the top 3.",
                f"{r.title} Draws ${pta:,.0f} per Theater in Limited Release",
                f"{r.title}'s Per-Theater Average Outpaces the Top of the Chart",
                ["per_theater_average", "limited_release"]))
    return out


def weekend_change_zscore(weeks: list[Weekend], cohort_weeks: dict | None = None) -> list[DiscoveryItem]:
    """Weekend-over-weekend change far from films at the same release week (|z| ≥ 2, cohort ≥ 8)."""
    if len(weeks) < 2:
        return []
    cur, prev = weeks[-1], weeks[-2]
    pb = prev.by_key()
    changes = {}
    for r in cur.rows:
        p = pb.get(r.film_key)
        if p and p.gross and r.gross is not None and r.days_in_release:
            changes[r.film_key] = (r, p, 100 * (r.gross - p.gross) / p.gross, (r.days_in_release - 1) // 7 + 1)
    out = []
    pool = cohort_weeks or {}
    for key, (r, p, chg, week_no) in changes.items():
        cohort = [c for k, (_, _, c, wk) in changes.items() if k != key and wk == week_no] + pool.get(week_no, [])
        if len(cohort) < MIN_COHORT:
            continue
        mu, sd = statistics.mean(cohort), statistics.pstdev(cohort)
        if not sd:
            continue
        z = (chg - mu) / sd
        if abs(z) >= Z_THRESHOLD:
            direction = "held far better" if z > 0 else "fell far harder"
            out.append(DiscoveryItem(
                "weekend_change_zscore", cur.weekend_start, key, r.title, "weekend_change_pct", round(chg, 1),
                {"cohort_mean": round(mu, 1), "cohort_sd": round(sd, 1), "z": round(z, 2), "week_number": week_no,
                 "cohort_n": len(cohort)},
                [_claim(cur, r, "weekend_gross", r.gross), _claim(prev, p, "weekend_gross", p.gross)],
                f"{r.title} changed {chg:+.1f}% weekend to weekend in week {week_no}; films in the same week averaged "
                f"{mu:+.1f}% (z = {z:+.1f}, n = {len(cohort)}).",
                f"{r.title} {'Holds Unusually Well' if z > 0 else 'Drops Sharply'} in Weekend {week_no}",
                f"{r.title}'s Weekend {week_no} Change Stands Out", ["retention", "cohort_outlier",
                                                                       "strong_hold" if z > 0 else "steep_drop"]))
    return out


def expansion_surge(weeks: list[Weekend]) -> list[DiscoveryItem]:
    if len(weeks) < 2:
        return []
    cur, prev = weeks[-1], weeks[-2]
    pb = prev.by_key()
    out = []
    for r in cur.rows:
        p = pb.get(r.film_key)
        if not (p and p.theaters and r.theaters and p.gross and r.gross):
            continue
        t_chg = 100 * (r.theaters - p.theaters) / p.theaters
        if t_chg >= EXPANSION_PCT and r.gross > p.gross:
            out.append(DiscoveryItem(
                "expansion_surge", cur.weekend_start, r.film_key, r.title, "theater_change_pct", round(t_chg, 1),
                {"previous_theaters": p.theaters, "theaters": r.theaters},
                [_claim(cur, r, "theaters", r.theaters), _claim(prev, p, "theaters", p.theaters),
                 _claim(cur, r, "weekend_gross", r.gross), _claim(prev, p, "weekend_gross", p.gross)],
                f"{r.title} expanded from {p.theaters:,} to {r.theaters:,} theaters ({t_chg:+.0f}%) and its weekend gross "
                f"rose from {money(p.gross)} to {money(r.gross)}.",
                f"{r.title} Expands to {r.theaters:,} Theaters and Grows", f"{r.title}'s Expansion Pays Off",
                ["expansion", "platform_release"]))
    return out


def format_contrast(w: Weekend, metadata: dict) -> list[DiscoveryItem]:
    """A concert film, documentary or Japanese animation in the top 5 (needs TMDB metadata by tmdb_id)."""
    out = []
    for r in w.rows:
        meta = metadata.get(r.tmdb_id) if r.tmdb_id else None
        if not meta or not r.source_rank or r.source_rank > 5:
            continue
        genres = {g.lower() for g in meta.get("genres", [])}
        fmt = ("concert/music" if "music" in genres else "documentary" if "documentary" in genres
               else "anime" if "animation" in genres and meta.get("origin_country") == "JP" else None)
        if fmt:
            out.append(DiscoveryItem(
                "format_contrast", w.weekend_start, r.film_key, r.title, "source_rank", r.source_rank,
                {"format": fmt, "format_basis": "TMDB genres/origin"},
                [_claim(w, r, "source_rank", r.source_rank), _claim(w, r, "weekend_gross", r.gross)],
                f"{r.title} ({fmt}, per TMDB) ranked No. {r.source_rank} with {money(r.gross)} among wide releases.",
                f"{r.title} Cracks the Weekend Top 5", f"A {fmt.title()} Release Lands in the Top 5",
                ["format_contrast", fmt.replace("/", "_")]))
    return out
