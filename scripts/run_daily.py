"""Faramir daily pipeline entrypoint (spec §5)."""

import logging
import os
import re
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

_MONEY_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*(billion|million|bn|b|m)?\b", re.I)


def _extract_money_values(text: str) -> list[float]:
    """Extract dollar figures mentioned in free text, normalized to raw USD."""
    values = []
    for amount_str, unit in _MONEY_RE.findall(text or ""):
        try:
            amount = float(amount_str.replace(",", ""))
        except ValueError:
            continue
        unit = (unit or "").lower()
        if unit.startswith("b"):
            amount *= 1_000_000_000
        elif unit.startswith("m"):
            amount *= 1_000_000
        values.append(amount)
    return values


def _validate_pick_figures(pick: dict, comp_film: dict | None) -> str | None:
    """Cross-check the comp film's block figures for internal consistency and
    against the prose Claude wrote. Returns a reason string if the pick should
    be dropped, or None if it's fine.

    Two checks, both conservative to avoid false positives:
      1. Worldwide must be >= domestic for the comp film — a hard data-integrity
         invariant. A violation means the corpus figure is wrong (e.g. a partial
         theatrical run rather than a lifetime aggregate).
      2. If the prose cites a dollar figure far larger (>1.5x) than both the
         comp's domestic and worldwide block figures, the block is very likely
         reading a stale/partial number while Claude's prose reflects the real
         (larger) lifetime gross from its training knowledge.
    """
    if not comp_film:
        return None

    comp_dom = int(comp_film.get("domestic_lifetime") or 0)
    comp_ww = int(comp_film.get("worldwide_lifetime") or 0)

    if comp_ww and comp_dom and comp_ww < comp_dom:
        return f"comp WW ({comp_ww}) < comp Dom ({comp_dom}) — partial-run data"

    prose = f"{pick.get('headline', '')} {pick.get('angle', '')}"
    mentioned = _extract_money_values(prose)
    if mentioned and (comp_ww or comp_dom):
        max_mentioned = max(mentioned)
        if max_mentioned > comp_ww * 1.5 and max_mentioned > comp_dom * 1.5:
            return (
                f"prose cites ${max_mentioned:,.0f} but comp block shows "
                f"WW={comp_ww}, Dom={comp_dom} — block/prose disagreement"
            )
    return None


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
        flush_usage_to_sheet,
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
    # The Numbers sometimes lags 1-2 days; fall back to the day before yesterday
    # if yesterday's page returns a 404 or empty result.
    daily = fetch_daily_list(yesterday)
    bom_date_used = yesterday
    if not daily:
        two_days_ago = today - timedelta(days=2)
        logger.warning(
            "No daily data for %s — falling back to %s", yesterday, two_days_ago
        )
        daily = fetch_daily_list(two_days_ago)
        bom_date_used = two_days_ago
    if not daily:
        raise RuntimeError(
            f"No daily BOM data for {yesterday} or {two_days_ago} — aborting"
        )
    if bom_date_used != yesterday:
        logger.info("Using BOM data from %s (yesterday unavailable)", bom_date_used)

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
            prior_dom = int(row.get("domestic_today") or 0)
            prior_stale = int(row.get("dom_stale_days") or 0)
            # Cumulative gross must never decrease vs the prior run — a lower
            # value means a bad scrape, not a real drop. Reject it and keep
            # the prior figure rather than let bad data through.
            if prior_dom and gross_today < prior_dom:
                logger.warning(
                    "'%s': domestic_today %s < prior %s — rejecting, keeping prior value",
                    bom_title, gross_today, prior_dom,
                )
                gross_today = prior_dom
            row["domestic_yesterday"] = prior_dom
            row["domestic_today"] = gross_today
            row["dom_stale_days"] = prior_stale + 1 if gross_today == prior_dom else 0
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
                details = {"tmdb_id": tmdb_id, "imdb_id": "", "title": bom_title, "release_date": "", "release_year": ""}
            row = {
                "tmdb_id": tmdb_id,
                "imdb_id": details.get("imdb_id", ""),
                "canonical_title": details.get("title", bom_title),
                "bom_title_seen": bom_title,
                "release_date": details.get("release_date", "") or details.get("release_year", ""),
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
                "dom_stale_days": 0,
                "ww_stale_days": 0,
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
        prior_ww = int(row.get("worldwide_today") or 0)
        prior_ww_stale = int(row.get("ww_stale_days") or 0)
        ww_data = scrape_worldwide(imdb_id)
        if ww_data:
            new_ww = ww_data.get("worldwide", 0)
            # Same decrease guard as domestic — a lower WW figure vs the
            # prior run means a bad scrape (e.g. a snapshot mid-aggregation),
            # not a real drop. Reject it and keep the prior value.
            if prior_ww and new_ww < prior_ww:
                logger.warning(
                    "tmdb_id=%s: worldwide_today %s < prior %s — rejecting, keeping prior value",
                    row.get("tmdb_id"), new_ww, prior_ww,
                )
                new_ww = prior_ww
            row["worldwide_yesterday"] = prior_ww
            row["worldwide_today"] = new_ww
            row["ww_stale_days"] = prior_ww_stale + 1 if new_ww == prior_ww else 0
        else:
            row["worldwide_yesterday"] = prior_ww
            row["ww_stale_days"] = prior_ww_stale + 1
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

    # Suppress films whose domestic or worldwide gross has been frozen for
    # 2+ consecutive runs (>48h at this daily cadence) — a field that never
    # updates means the source stopped reporting for this title, not that
    # movement genuinely stalled overnight. Picking off a stale figure risks
    # publishing an inflated/outdated comparison.
    stale_films = [
        r for r in active_films_raw
        if int(r.get("dom_stale_days") or 0) >= 2 or int(r.get("ww_stale_days") or 0) >= 2
    ]
    if stale_films:
        logger.warning(
            "Suppressing %d film(s) with stale gross data: %s",
            len(stale_films),
            [r.get("canonical_title") for r in stale_films],
        )
    active_films_raw = [
        r for r in active_films_raw
        if int(r.get("dom_stale_days") or 0) < 2 and int(r.get("ww_stale_days") or 0) < 2
    ]

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
            raw_comp_id = s.get("comp_tmdb_id", "")
            threshold = s.get("threshold_value", "")
            # Sheets may store Python None as the literal string "None" — treat that as absent
            comp_id_str = str(raw_comp_id).strip() if raw_comp_id is not None else ""
            if comp_id_str in ("", "None", "0"):
                comp_id_str = ""
            if comp_id_str:
                try:
                    suppressed_pairs.add((
                        int(s.get("active_tmdb_id") or 0),
                        int(comp_id_str),
                        s.get("axis", ""),
                    ))
                except ValueError:
                    pass
            elif threshold and str(threshold).strip() not in ("", "None"):
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

        # Does this active film itself carry a Cerebro signal (director, lead
        # cast, or franchise already flagged as high-traffic)? This is the
        # only input to editorial "importance" tiering — no fixed film list.
        film_dirs = [d.strip() for d in (film.get("director") or "").split(",") if d.strip()]
        film_cast = (film.get("lead_cast") or [])[:3]
        film_collection = film.get("collection_name")
        signal_names = film_dirs + film_cast + ([film_collection] if film_collection else [])
        active_film_is_signal = any(
            cerebro_signals.get(name, {}).get("is_signal") for name in signal_names
        )

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
            "is_signal": active_film_is_signal,
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

    # Step 11b: Validate comp-figure integrity before anything gets posted —
    # drop picks that fail the WW>=Dom invariant or show a block/prose mismatch
    # rather than shipping a pick a writer would copy the wrong number from.
    corpus_by_tmdb = {f.get("tmdb_id"): f for f in corpus}
    validated_picks = []
    for pick in picks:
        comp_tmdb = pick.get("comp_tmdb_id")
        comp_film = corpus_by_tmdb.get(comp_tmdb) if comp_tmdb else None
        reason = _validate_pick_figures(pick, comp_film)
        if reason:
            logger.warning(
                "Dropping pick '%s' vs '%s': %s",
                pick.get("active_title", ""), pick.get("comp_title", ""), reason,
            )
            continue
        validated_picks.append(pick)
    picks = validated_picks

    # Step 12: Post to Slack
    post_header(slack_url, len(picks), len(enriched_active), today)

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

    # Step 14: Flush Claude usage records to the Usage sheet tab (fails open)
    flush_usage_to_sheet(gc, sheet_id)

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
