"""Faramir daily pipeline entrypoint (spec §5)."""

import logging
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("faramir.daily")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main():
    from faramir import config
    from faramir.bom_fetch import fetch_daily_list, fetch_weekend_list, scrape_worldwide, normalize_title
    from faramir.corpus import load_corpus, build_index
    from faramir.matcher import find_all_candidates
    from faramir.milestone import find_milestones
    from faramir.cerebro import load_cerebro, build_talent_signals, build_franchise_signals
    from faramir.sheet import (
        get_sheet_client,
        read_state,
        write_state,
        read_suggestions,
        append_suggestions,
    )
    from faramir.claude_select import build_payload, select_picks
    from faramir.slack_post import post_header, post_pick, post_init, post_failure
    from faramir.tmdb import resolve_tmdb_id, get_movie_details
    from faramir.feedback import collect_feedback
    from faramir.sheet import write_suggestions

    sheet_id = os.environ["FARAMIR_SHEET_ID"]
    cerebro_sheet_id = os.environ.get("CEREBRO_MW_SHEET_ID", "")
    slack_url = os.environ["SLACK_WEBHOOK_URL"]
    slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    slack_channel_id = os.environ.get("SLACK_CHANNEL_ID", "")
    workflow_url = os.environ.get("GITHUB_WORKFLOW_URL", "")

    today = date.today()
    yesterday = today - timedelta(days=1)
    is_monday = today.weekday() == 0

    logger.info("=== Faramir daily run: %s ===", today)

    # Step 1: Connect to sheets
    gc = get_sheet_client()

    # Step 1b: Collect feedback on yesterday's picks (before anything else)
    if slack_bot_token and slack_channel_id:
        try:
            all_suggestions = read_suggestions(gc, sheet_id)
            updated = collect_feedback(slack_bot_token, slack_channel_id, all_suggestions, yesterday)
            if updated != all_suggestions:
                write_suggestions(gc, sheet_id, updated)
                logger.info("Feedback collected and written for %s", yesterday)
        except Exception as exc:
            logger.warning("Feedback collection failed (non-fatal): %s", exc)
    else:
        logger.info("SLACK_BOT_TOKEN or SLACK_CHANNEL_ID not set — skipping feedback collection")

    # Step 2: Load state tab — detect cold start
    state_rows = read_state(gc, sheet_id)
    cold_start = len(state_rows) == 0
    state_by_tmdb: dict[int, dict] = {}
    for r in state_rows:
        try:
            state_by_tmdb[int(r["tmdb_id"])] = r
        except (ValueError, KeyError):
            pass

    # Step 3: Fetch BOM daily data (and weekend on Mondays)
    daily = fetch_daily_list(yesterday)
    if not daily:
        raise RuntimeError(f"No daily BOM data returned for {yesterday} — aborting")

    if is_monday:
        iso = yesterday.isocalendar()
        weekend = fetch_weekend_list(iso.year, iso.week)
        logger.info("Monday: fetched %d weekend entries", len(weekend))
        # Merge weekend entries for any titles not already in daily
        daily_titles = {normalize_title(e["title"]) for e in daily}
        for w in weekend:
            if normalize_title(w["title"]) not in daily_titles:
                daily.append(w)

    # Step 4: Resolve / update state for each BOM title
    new_state: dict[int, dict] = {}

    for entry in daily:
        bom_title = entry["title"]
        days = int(entry.get("days_in_release") or 0)
        gross_today = int(entry.get("gross_to_date") or 0)
        distributor = entry.get("distributor", "")

        if gross_today < config.ACTIVE_FLOOR_USD:
            continue
        if days > config.MAX_DAYS_IN_RELEASE:
            continue

        # Match to existing state row by normalized title
        norm = normalize_title(bom_title)
        matched_row = None
        for row in state_by_tmdb.values():
            if (normalize_title(row.get("bom_title_seen", "")) == norm
                    or normalize_title(row.get("canonical_title", "")) == norm):
                matched_row = row
                break

        if matched_row:
            tmdb_id = int(matched_row["tmdb_id"])
            row = dict(matched_row)
            row["domestic_yesterday"] = int(row.get("domestic_today") or 0)
            row["domestic_today"] = gross_today
            row["days_in_release"] = days
            row["last_seen"] = str(today)
            row["status"] = "active"
            row["bom_title_seen"] = bom_title
        else:
            logger.info("New film: '%s' — resolving via TMDB", bom_title)
            tmdb_match = resolve_tmdb_id(bom_title)
            if not tmdb_match:
                logger.warning("Could not resolve TMDB for '%s', skipping", bom_title)
                continue
            tmdb_id = tmdb_match["id"]
            try:
                details = get_movie_details(tmdb_id)
            except Exception as exc:
                logger.warning("TMDB details failed for tmdb_id=%s: %s", tmdb_id, exc)
                details = {"tmdb_id": tmdb_id, "imdb_id": "", "title": bom_title, "release_year": ""}
            row = {
                "tmdb_id": tmdb_id,
                "imdb_id": details.get("imdb_id", ""),
                "canonical_title": details.get("title", bom_title),
                "bom_title_seen": bom_title,
                "release_date": str(details.get("release_year", "")),
                "first_seen": str(today),
                "last_seen": str(today),
                "status": "active",
                "domestic_yesterday": 0,
                "domestic_today": gross_today,
                "worldwide_yesterday": 0,
                "worldwide_today": 0,
                "days_in_release": days,
                "tmdb_id_override": "",
                "notes": "",
            }

        new_state[tmdb_id] = row

    # Films in old state but not seen today: mark retired if movement stalled
    for tmdb_id, row in state_by_tmdb.items():
        if tmdb_id in new_state:
            continue
        row = dict(row)
        dom_today = int(row.get("domestic_today") or 0)
        dom_yest = int(row.get("domestic_yesterday") or 0)
        first_seen_str = row.get("first_seen", "")
        try:
            first_seen = date.fromisoformat(first_seen_str)
        except ValueError:
            first_seen = today - timedelta(days=100)
        grace = (today - first_seen).days < config.NEW_FILM_GRACE_DAYS
        moved = dom_yest > 0 and (dom_today - dom_yest) / dom_yest > config.MIN_MOVEMENT_PCT_7D
        if not grace and not moved:
            row["status"] = "retired"
        new_state[tmdb_id] = row

    # Step 5: Scrape worldwide for active films that have an imdb_id
    for row in new_state.values():
        if row.get("status") != "active":
            continue
        imdb_id = row.get("imdb_id")
        if not imdb_id:
            continue
        ww_data = scrape_worldwide(imdb_id)
        if ww_data:
            row["worldwide_yesterday"] = int(row.get("worldwide_today") or 0)
            row["worldwide_today"] = ww_data.get("worldwide", 0)
        else:
            row["worldwide_yesterday"] = int(row.get("worldwide_today") or 0)
            # Leave worldwide_today unchanged from state

    if cold_start:
        logger.info("Cold start detected — seeding state, skipping picks")
        write_state(gc, sheet_id, list(new_state.values()))
        post_init(slack_url, sum(1 for r in new_state.values() if r.get("status") == "active"))
        return

    # Active films that have a yesterday baseline (first_seen < today)
    active_films_raw = [
        r for r in new_state.values()
        if r.get("status") == "active"
        and r.get("first_seen", str(today)) < str(today)
    ]
    logger.info("%d active films with yesterday baseline", len(active_films_raw))

    # Step 6: Enrich active films with full TMDB metadata
    enriched_active: list[dict] = []
    for row in active_films_raw:
        tmdb_id = row.get("tmdb_id")
        if not tmdb_id:
            continue
        try:
            details = get_movie_details(int(tmdb_id))
            combined = {**details, **row}  # state values win for gross fields
        except Exception as exc:
            logger.warning("TMDB enrich failed for tmdb_id=%s: %s", tmdb_id, exc)
            combined = dict(row)
        enriched_active.append(combined)

    # Step 7: Load corpus
    corpus_path = os.path.join(os.path.dirname(__file__), "..", "data", "corpus.json")
    corpus = load_corpus(corpus_path)
    if not corpus:
        logger.warning("Corpus is empty — no comp matching possible today")

    # Step 8: Load Cerebro signals
    cerebro_rows = []
    if cerebro_sheet_id:
        try:
            cerebro_rows = load_cerebro(gc, cerebro_sheet_id)
            logger.info("Cerebro: loaded %d rows", len(cerebro_rows))
        except Exception as exc:
            logger.warning("Cerebro sheet unreachable: %s — continuing without signals", exc)

    all_talent_names: list[str] = []
    all_collection_names: list[str] = []
    for film in enriched_active:
        dirs = [d.strip() for d in (film.get("director") or "").split(",") if d.strip()]
        cast = (film.get("lead_cast") or [])[:3]
        all_talent_names.extend(dirs + cast)
        if film.get("collection_name"):
            all_collection_names.append(film["collection_name"])

    talent_signals = build_talent_signals(cerebro_rows, list(set(all_talent_names)))
    franchise_signals = build_franchise_signals(cerebro_rows, list(set(all_collection_names)))
    cerebro_signals = {**talent_signals, **franchise_signals}

    # Step 9: Build suppressed pairs from recent suggestions
    suppress_cutoff = today - timedelta(days=config.SUPPRESS_DAYS)
    past_suggestions = read_suggestions(gc, sheet_id)
    suppressed_pairs: set[tuple] = set()
    for s in past_suggestions:
        try:
            run_d = date.fromisoformat(str(s.get("run_date", "")))
        except ValueError:
            continue
        if run_d >= suppress_cutoff:
            comp_id = s.get("comp_tmdb_id")
            threshold = s.get("threshold_value")
            if comp_id:
                suppressed_pairs.add((
                    int(s.get("active_tmdb_id") or 0),
                    int(comp_id),
                    s.get("axis", ""),
                ))
            elif threshold:
                # milestone suppression key uses 0 for comp_tmdb_id
                suppressed_pairs.add((
                    int(s.get("active_tmdb_id") or 0),
                    0,
                    s.get("axis", ""),
                ))

    # Step 10: Find candidates and milestones
    candidates = find_all_candidates(enriched_active, corpus, cerebro_signals, suppressed_pairs)
    logger.info("Matcher: %d total candidates across all active films", len(candidates))

    # Build per-film candidate lists for the Claude payload
    active_films_payload: list[dict] = []
    for film in enriched_active:
        fid = film.get("tmdb_id")
        film_candidates = [c for c in candidates if c.get("active_tmdb_id") == fid]

        # Milestone candidates for this film
        film_milestones = find_milestones(film)
        # Apply suppression to milestones
        for m in film_milestones:
            key = (fid, 0, m.get("axis", ""))
            if key in suppressed_pairs:
                m["suppressed"] = True

        all_film_candidates = film_candidates + film_milestones

        active_films_payload.append({
            "tmdb_id": fid,
            "title": film.get("canonical_title") or film.get("title"),
            "release_date": str(film.get("release_date") or film.get("release_year") or ""),
            "days_in_release": film.get("days_in_release"),
            "distributor": film.get("distributor", ""),
            "director": film.get("director", ""),
            "lead_cast": film.get("lead_cast") or [],
            "genres": film.get("genres") or [],
            "collection_id": film.get("collection_id"),
            "collection_name": film.get("collection_name"),
            "domestic_yesterday": film.get("domestic_yesterday", 0),
            "domestic_today": film.get("domestic_today", 0),
            "worldwide_yesterday": film.get("worldwide_yesterday", 0),
            "worldwide_today": film.get("worldwide_today"),
            "candidates": all_film_candidates,
        })

    # Step 11: Claude selection
    dow = today.weekday()
    picks_target = config.PICKS_TARGET.get(dow, 4)
    picks_min = config.PICKS_MIN_BY_DAY.get(dow, 2)
    picks_max = config.PICKS_MAX_BY_DAY.get(dow, 4)

    picks: list[dict] = []
    if active_films_payload and any(f["candidates"] for f in active_films_payload):
        payload = build_payload(today, active_films_payload, picks_target, picks_min, picks_max)
        picks = select_picks(payload)
        if picks is None:
            picks = []
    else:
        logger.warning("No candidates to send to Claude today")

    # Step 12: Post to Slack
    post_header(slack_url, len(picks), len(enriched_active), today)

    corpus_by_tmdb = {f.get("tmdb_id"): f for f in corpus}
    suggestion_rows: list[dict] = []

    for pick in picks:
        comp_tmdb = pick.get("comp_tmdb_id")
        comp_film = corpus_by_tmdb.get(comp_tmdb) if comp_tmdb else None
        active_tmdb = pick.get("active_tmdb_id")
        active_film_match = next((f for f in enriched_active if f.get("tmdb_id") == active_tmdb), {})

        slack_ts = None
        if slack_bot_token and slack_channel_id:
            slack_ts = post_pick(slack_bot_token, slack_channel_id, pick, active_film_match, comp_film)
        else:
            logger.warning("No SLACK_BOT_TOKEN/SLACK_CHANNEL_ID — pick not posted to Slack")

        suggestion_rows.append({
            "run_date": str(today),
            "active_tmdb_id": pick.get("active_tmdb_id", ""),
            "active_title": pick.get("active_title", ""),
            "comp_tmdb_id": pick.get("comp_tmdb_id", ""),
            "comp_title": pick.get("comp_title", ""),
            "axis": pick.get("axis", ""),
            "kind": pick.get("kind", ""),
            "threshold_value": pick.get("threshold_value", ""),
            "angle_category": pick.get("angle_category", ""),
            "headline": pick.get("headline", ""),
            "angle": pick.get("angle", ""),
            "richness_score": pick.get("richness_score", ""),
            "slack_ts": slack_ts or "",
            "thumbs_up": "",
            "thumbs_down": "",
            "thread_replies": "",
            "thread_feedback": "",
        })

    # Step 13: Write state and log suggestions
    if suggestion_rows:
        append_suggestions(gc, sheet_id, suggestion_rows)

    write_state(gc, sheet_id, list(new_state.values()))
    logger.info("=== Faramir complete: %d picks logged ===", len(picks))


if __name__ == "__main__":
    _slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    _workflow_url = os.environ.get("GITHUB_WORKFLOW_URL", "")
    try:
        main()
    except Exception as _exc:
        logger.exception("Faramir daily run failed with unhandled exception")
        if _slack_url:
            try:
                from faramir.slack_post import post_failure
                post_failure(_slack_url, _exc, _workflow_url)
            except Exception:
                pass
        sys.exit(1)
