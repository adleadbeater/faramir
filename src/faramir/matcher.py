from __future__ import annotations
"""Candidate pair generation and scoring for Faramir (spec §9-13)."""

import logging
from faramir.config import (
    ABOUT_TO_PASS_PCT,
    JUST_PASSED_BUFFER_PCT,
    CANDIDATES_PER_FILM_PER_CELL,
    CANDIDATES_PER_FILM_MAX,
    MIN_RICHNESS_TO_SURFACE,
    ASYMMETRY_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Scoring weights per §11
SCORE_WEIGHTS = {
    "franchise_sibling": 10,
    "creative_head_to_head_director": 8,
    "creative_head_to_head_cast": 6,
    "genre_milestone": 5,
    "studio_sibling": 2,
    "same_origin": 1,
    "unexpected_giant_killer": 7,
}
BONUS_BEST_PICTURE = 3
BONUS_CLASSIC = 2
BONUS_JUST_PASSED = 3
BONUS_ABOUT_TO_PASS = 1
BONUS_TALENT_SIGNAL = 2
PENALTY_SUPPRESSED = -100


def detect_angle_categories(active_film: dict, comp: dict, corpus: list[dict]) -> list[str]:
    """Detect which angle categories apply to this pair per §10."""
    categories = []

    # Franchise sibling: same collection_id (non-null)
    active_coll = active_film.get("collection_id")
    comp_coll = comp.get("collection_id")
    if active_coll and comp_coll and active_coll == comp_coll:
        categories.append("franchise_sibling")

    # Creative head-to-head: shared director
    active_dirs = set(d.strip() for d in (active_film.get("director") or "").split(",") if d.strip())
    comp_dirs = set(d.strip() for d in (comp.get("director") or "").split(",") if d.strip())
    if active_dirs & comp_dirs:
        categories.append("creative_head_to_head_director")

    # Creative head-to-head: shared lead cast
    active_cast = set(active_film.get("lead_cast") or [])
    comp_cast = set(comp.get("lead_cast") or [])
    if active_cast & comp_cast:
        categories.append("creative_head_to_head_cast")

    # Studio sibling: same distributor
    active_dist = (active_film.get("distributor") or "").strip().lower()
    comp_dist = (comp.get("distributor") or "").strip().lower()
    if active_dist and comp_dist and active_dist == comp_dist:
        categories.append("studio_sibling")

    # Same origin country
    if active_film.get("origin_country") and active_film.get("origin_country") == comp.get("origin_country"):
        categories.append("same_origin")

    # Genre milestone: comp is top-grossing in same genre
    active_genres = set(g.lower() for g in (active_film.get("genres") or []))
    comp_genres = set(g.lower() for g in (comp.get("genres") or []))
    if active_genres & comp_genres:
        # Check if comp is the #1 or #2 in that genre by worldwide_lifetime
        shared_genre = list(active_genres & comp_genres)[0]
        genre_top = sorted(
            [f for f in corpus if shared_genre in [g.lower() for g in f.get("genres", [])]],
            key=lambda f: f.get("worldwide_lifetime", 0),
            reverse=True,
        )[:5]
        comp_tmdb = comp.get("tmdb_id")
        if any(f.get("tmdb_id") == comp_tmdb for f in genre_top):
            categories.append("genre_milestone")

    # Unexpected giant killer: no overlap on franchise/director/cast/genre AND comp is notable
    no_franchise_overlap = not (active_coll and comp_coll and active_coll == comp_coll)
    no_director_overlap = not (active_dirs & comp_dirs)
    no_cast_overlap = not (active_cast & comp_cast)
    no_genre_overlap = not (active_genres & comp_genres)
    comp_notable = (
        comp.get("worldwide_lifetime", 0) > 500_000_000
        or comp.get("is_best_picture", False)
        or comp.get("is_classic", False)
    )
    if no_franchise_overlap and no_director_overlap and no_cast_overlap and no_genre_overlap and comp_notable:
        categories.append("unexpected_giant_killer")

    return categories


def score_candidate(
    active_film: dict,
    comp: dict,
    axis: str,
    kind: str,
    angle_categories: list[str],
    cerebro_signals: dict,
) -> int:
    """Compute richness score per §11."""
    score = 0

    # Angle priority: franchise_sibling > creative_head_to_head > studio_sibling
    # creative wins over studio if both present
    if "franchise_sibling" in angle_categories:
        score += SCORE_WEIGHTS["franchise_sibling"]
    elif "creative_head_to_head_director" in angle_categories:
        score += SCORE_WEIGHTS["creative_head_to_head_director"]
        # Skip studio_sibling if creative present
    elif "creative_head_to_head_cast" in angle_categories:
        score += SCORE_WEIGHTS["creative_head_to_head_cast"]
    elif "studio_sibling" in angle_categories:
        score += SCORE_WEIGHTS["studio_sibling"]

    if "genre_milestone" in angle_categories:
        score += SCORE_WEIGHTS["genre_milestone"]

    if "same_origin" in angle_categories:
        score += SCORE_WEIGHTS["same_origin"]

    if "unexpected_giant_killer" in angle_categories:
        score += SCORE_WEIGHTS["unexpected_giant_killer"]

    # Comp bonuses
    if comp.get("is_best_picture"):
        score += BONUS_BEST_PICTURE
    if comp.get("is_classic"):
        score += BONUS_CLASSIC

    # Kind bonuses
    if kind == "just_passed":
        score += BONUS_JUST_PASSED
    elif kind == "about_to_pass":
        score += BONUS_ABOUT_TO_PASS

    # Talent signal bonus
    comp_director = comp.get("director", "")
    comp_cast = comp.get("lead_cast") or []
    talent_names = [comp_director] + comp_cast
    for name in talent_names:
        sig = cerebro_signals.get(name, {})
        if sig.get("is_signal"):
            score += BONUS_TALENT_SIGNAL
            break  # One bonus per comp

    return score


def detect_asymmetry(active_film: dict, corpus: list[dict]) -> dict | None:
    """Check if active film's WW/dom ratio deviates >25% from genre+decade cohort."""
    dom = active_film.get("domestic_today", 0) or active_film.get("domestic_lifetime", 0)
    ww = active_film.get("worldwide_today", 0) or active_film.get("worldwide_lifetime", 0)
    if not dom or not ww:
        return None

    active_ratio = ww / dom
    genres = [g.lower() for g in (active_film.get("genres") or [])]
    release_year = int(active_film.get("release_year") or 0)
    decade = (release_year // 10) * 10 if release_year else None

    cohort = []
    for film in corpus:
        f_dom = film.get("domestic_lifetime", 0)
        f_ww = film.get("worldwide_lifetime", 0)
        if not f_dom or not f_ww:
            continue
        f_genres = [g.lower() for g in (film.get("genres") or [])]
        f_year = int(film.get("release_year") or 0)
        f_decade = (f_year // 10) * 10 if f_year else None
        genre_match = bool(set(genres) & set(f_genres))
        decade_match = decade and f_decade and decade == f_decade
        if genre_match and decade_match:
            cohort.append(f_ww / f_dom)

    if len(cohort) < 5:
        return None

    cohort_median = sorted(cohort)[len(cohort) // 2]
    if cohort_median == 0:
        return None

    deviation = abs(active_ratio - cohort_median) / cohort_median
    if deviation > ASYMMETRY_THRESHOLD:
        return {
            "active_ratio": active_ratio,
            "cohort_median": cohort_median,
            "deviation_pct": deviation,
        }
    return None


def find_candidates(active_film: dict, corpus_index: dict, corpus: list[dict]) -> list[dict]:
    """Generate all candidate pairs for one active film."""
    from faramir.corpus import films_in_range

    candidates = []
    cells: dict[str, list[dict]] = {}  # key: f"{axis}_{kind}"

    for axis in ("domestic", "worldwide"):
        today = active_film.get(f"{axis}_today", 0) or 0
        yesterday = active_film.get(f"{axis}_yesterday", 0) or 0
        if not today:
            continue

        # just_passed: today >= threshold*(1-JUST_PASSED_BUFFER_PCT) and yesterday < threshold
        # about_to_pass: today >= threshold*(1-ABOUT_TO_PASS_PCT) and today < threshold
        # We search for corpus films near the active film's current gross

        # Search range covers both kinds:
        #   just_passed  → comp_val in [yesterday, today * (1 + JUST_PASSED_BUFFER_PCT)]
        #   about_to_pass → comp_val in (today, today * (1 + ABOUT_TO_PASS_PCT)]
        # Use the widest bounds so a single range query catches both.
        lo_search = int(min(yesterday or today * (1 - ABOUT_TO_PASS_PCT), today * (1 - ABOUT_TO_PASS_PCT)))
        hi_search = int(today * (1 + ABOUT_TO_PASS_PCT))  # ABOUT_TO_PASS_PCT > JUST_PASSED_BUFFER_PCT

        nearby = films_in_range(corpus_index, axis, lo_search, hi_search)

        for comp in nearby:
            if comp.get("tmdb_id") == active_film.get("tmdb_id"):
                continue
            comp_val = comp.get(f"{axis}_lifetime", 0)
            if not comp_val:
                continue

            # Determine kind
            if comp_val <= today and yesterday < comp_val:
                kind = "just_passed"
            elif comp_val > today and comp_val <= today * (1 + ABOUT_TO_PASS_PCT):
                kind = "about_to_pass"
            else:
                continue

            cell_key = f"{axis}_{kind}"
            cells.setdefault(cell_key, [])
            if len(cells[cell_key]) >= CANDIDATES_PER_FILM_PER_CELL:
                continue

            angle_categories = detect_angle_categories(active_film, comp, corpus)

            candidate = {
                "active_tmdb_id": active_film.get("tmdb_id"),
                "active_title": active_film.get("canonical_title") or active_film.get("title"),
                "comp_tmdb_id": comp.get("tmdb_id"),
                "comp_title": comp.get("title"),
                "axis": axis,
                "kind": kind,
                "threshold_value": comp_val,
                "angle_categories": angle_categories,
                "comp": comp,
                "suppressed": False,
            }
            cells[cell_key].append(candidate)
            candidates.append(candidate)

    return candidates


def find_all_candidates(
    active_films: list[dict],
    corpus: list[dict],
    cerebro_signals: dict,
    suppressed_pairs: set,
) -> list[dict]:
    """Main entry: generate, score, filter, and return all candidates across all active films."""
    from faramir.corpus import build_index

    corpus_index = build_index(corpus)
    all_candidates = []

    for active_film in active_films:
        film_candidates = find_candidates(active_film, corpus_index, corpus)
        scored = []

        for cand in film_candidates:
            pair_key = (cand["active_tmdb_id"], cand["comp_tmdb_id"], cand["axis"])
            suppressed = pair_key in suppressed_pairs

            score = score_candidate(
                active_film,
                cand["comp"],
                cand["axis"],
                cand["kind"],
                cand["angle_categories"],
                cerebro_signals,
            )
            if suppressed:
                score += PENALTY_SUPPRESSED
                cand["suppressed"] = True

            cand["richness_score"] = score
            scored.append(cand)

        # Filter by minimum richness (unless suppressed)
        valid = [c for c in scored if not c["suppressed"] and c["richness_score"] >= MIN_RICHNESS_TO_SURFACE]

        # Cap total per film
        valid_sorted = sorted(valid, key=lambda c: c["richness_score"], reverse=True)
        valid_sorted = valid_sorted[:CANDIDATES_PER_FILM_MAX]

        all_candidates.extend(valid_sorted)

    return all_candidates
