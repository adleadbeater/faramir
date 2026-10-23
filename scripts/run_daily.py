"""Faramir daily pipeline entrypoint (spec §5)."""

import json
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

def _suppressed_pairs(past_suggestions: list[dict], today, suppress_days: int) -> set[tuple]:
    """Pairs suggested within the suppression window (unchanged pre-v2 logic)."""
    suppress_cutoff = today - timedelta(days=suppress_days)
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
    return suppressed_pairs


def _identity_overrides(gc, sheet_id: str):
    """Editor-owned ``identity_overrides`` tab (tn_slug, tmdb_id, note): read-only to Faramir.
    Missing tab = no overrides. Duplicates, conflicts and malformed rows: faramir.identity.parse_overrides."""
    from faramir.identity import Overrides, parse_overrides
    from faramir.sheet import TabMissing, read_values

    try:
        values = read_values(gc, sheet_id, "identity_overrides")
    except TabMissing:
        return Overrides()
    overrides = parse_overrides(values)
    for problem in overrides.problems:
        logger.warning(problem)
    return overrides


def _join_warnings(*warnings) -> str | None:
    parts = [w for w in warnings if w]
    return "\n".join(parts) if parts else None


def _sheet_client(ctx):
    """Production Sheets client, or the read-only fixture client offline."""
    if ctx.fixtures_dir:
        from faramir.offline import sheet_client

        return sheet_client()
    from faramir.sheet import get_sheet_client

    return get_sheet_client()


def _run(ctx, run_date=None):
    """The daily pipeline. Every external write goes through faramir.runtime."""
    from faramir import config
    from faramir import runtime
    from faramir import sheet as sheet_module
    from faramir.bom_fetch import fetch_daily_list, fetch_weekend_list, scrape_worldwide, normalize_title
    from faramir.corpus import ABORT, load_corpus_v2
    from faramir.matcher import find_all_candidates
    from faramir.milestone import find_milestones
    from faramir.cerebro import load_cerebro, build_talent_signals, build_franchise_signals
    from faramir.sheet import (
        SheetReadError,
        flush_usage_to_sheet,
    )
    from faramir.binding import bind_picks
    from faramir.claude_select import build_payload, select
    from faramir.slack_post import post_header, post_header_message, post_init, post_failure, post_corpus_abort, post_alert
    from faramir.delivery import (
        COUNTS_TOWARD_TODAY, DeliveryItem, SlackTarget, deliver, reconcile, should_post_header,
        suppression_records, triage_undelivered,
    )
    from faramir.state_integrity import ABORT as STATE_ABORT, PROCEED
    from faramir.state_store import StateConflict, StateRecoveryRefused, StateStore
    from faramir.tmdb import get_movie_details, get_release_dates, search_movie
    from faramir import identity as identity_mod
    from faramir.feedback import fetch_feedback
    from faramir.suggestions import (
        SuggestionsReadError, SuggestionsWriteIncident, append_new, apply_feedback, read_table as read_suggestions_table,
        suggestion_id as derive_suggestion_id, update_by_id,
    )
    from datetime import datetime, timezone

    sheet_id = os.environ.get("FARAMIR_SHEET_ID", "")
    cerebro_sheet_id = os.environ.get("CEREBRO_MW_SHEET_ID", "")
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    slack_channel_id = os.environ.get("SLACK_CHANNEL_ID", "")
    workflow_url = os.environ.get("GITHUB_WORKFLOW_URL", "")

    today = run_date or date.today()
    yesterday = today - timedelta(days=1)
    is_monday = today.weekday() == 0

    logger.info("=== Faramir daily run: %s (%s, run_id %s) ===", today, ctx.mode, ctx.run_id)
    runtime.note("run_date", str(today))

    # Step 0: Preflight (faramir.preflight) — before any read or write of
    # production systems. A failure halts the run: no operational write can
    # happen afterwards. Nothing is repaired or migrated here.
    from faramir import preflight

    settings = preflight.check_settings(live=not ctx.dry_run)
    if not settings.ok:
        ctx.halt("preflight: " + "; ".join(settings.problems))
        runtime.note("preflight", {**settings.as_dict(), "missing_settings": [
            p.removeprefix("missing required setting ") for p in settings.problems if p.startswith("missing")]})
        logger.error("Preflight failed: %s — stopping with no writes", settings.problems)
        if slack_url:
            post_alert(slack_url, f"Faramir {today}: preflight failed — stopped before any write", settings.problems)
        return
    # The schema-v2 comparison corpus: a legacy (v1) or corrupt file is never
    # used and never silently replaced. ABORT halts all operational writes
    # (state included) and sends one alert.
    corpus_path = os.environ.get("FARAMIR_CORPUS_PATH") or os.path.join(
        os.path.dirname(__file__), "..", "data", "corpus.v2.json"
    )
    corpus_load = load_corpus_v2(corpus_path, run_date=today)
    runtime.note("corpus", corpus_load.summary())
    if corpus_load.status == ABORT:
        ctx.halt("corpus ABORT: " + "; ".join(corpus_load.reasons))
        runtime.note("preflight", {"ok": False, "corpus": "ABORT"})
        post_corpus_abort(slack_url, corpus_load.reasons, today)
        logger.error("=== Faramir stopped: comparison corpus ABORT — no picks and no data writes ===")
        return

    # Step 1: Connect to sheets; verify the schema marker, tabs and headers.
    gc = _sheet_client(ctx)
    from faramir.history import collection_enabled

    sheets = preflight.check_sheets(gc, sheet_id, collect_history=collection_enabled())
    runtime.note("preflight", {"ok": sheets.ok, "corpus": corpus_load.status, "problems": sheets.problems,
                               "checked": ["settings", "corpus"] + sheets.checked})
    if not sheets.ok:
        ctx.halt("preflight: " + "; ".join(sheets.problems))
        post_alert(slack_url, f"Faramir {today}: preflight failed — stopped before any write", sheets.problems)
        logger.error("=== Faramir stopped: preflight %s ===", sheets.problems)
        return

    # Step 1a: State integrity, before any write. A read error, an
    # unexplained mismatch or another run holding the lock stops the run with
    # no writes. An interrupted write with a verified backup is recovered —
    # and that recovery run makes no picks.
    store = StateStore(gc, sheet_id, ctx.run_id)
    try:
        decision = store.open()
    except SheetReadError as exc:
        decision = None
        ctx.halt(f"state read failed: {exc}")
    runtime.note("state", {"decision": decision.action if decision else "READ_ERROR",
                           "reason": decision.reason if decision else ctx.halted_reason})
    if decision is not None and decision.action not in (PROCEED, STATE_ABORT):
        try:
            store.acquire_lock()
            try:
                message = store.recover()
            finally:
                store.release_lock()
        except (StateConflict, StateRecoveryRefused) as exc:
            ctx.halt(f"state recovery not performed: {exc}")
        else:
            runtime.note("state_recovery", message)
            ctx.halt(f"recovery run: {message} — no picks this run")
            post_alert(slack_url, f"Faramir {today}: state recovered — no picks this run", [message])
            return
    elif decision is not None and decision.action == STATE_ABORT:
        ctx.halt(f"state integrity: {decision.reason}")
    if ctx.halted_reason:
        post_alert(slack_url, f"Faramir {today}: stopped before any write — state needs attention", [ctx.halted_reason])
        logger.error("=== Faramir stopped: %s ===", ctx.halted_reason)
        return
    try:
        store.acquire_lock()
    except StateConflict as exc:
        ctx.halt(f"state lock: {exc}")
        post_alert(slack_url, f"Faramir {today}: another run is using the state — stopped", [str(exc)])
        return

    def _release_lock():
        try:
            store.release_lock()
        except Exception:
            logger.exception("Could not release the state lock (it expires on its own)")

    ctx.on_exit.append(_release_lock)

    def _stop(reason: str, title: str) -> None:
        """Halt after the lock is held: release it first, then alert."""
        _release_lock()
        ctx.halt(reason)
        post_alert(slack_url, f"Faramir {today}: {title}", [reason])
        logger.error("=== Faramir stopped: %s ===", reason)

    # Step 1b: Read the suggestions tab once. It drives suppression, so a read
    # failure stops the run (it used to silently disable suppression).
    try:
        suggestions_table = read_suggestions_table(gc, sheet_id)
    except (SheetReadError, SuggestionsReadError) as exc:
        _stop(f"suggestions read failed: {exc}", "cannot read the suggestions tab — stopped")
        return
    runtime.note("suggestions_table", suggestions_table.ambiguity_report())
    if suggestions_table.header and (suggestions_table.schema != 2 or any(r.sid is None for r in suggestions_table.rows)):
        _stop("suggestions tab is not on schema v2 (explicit suggestion_id + delivery columns); "
              "run scripts/migrate_suggestions.py — separately approved", "suggestions tab needs migration — stopped")
        return

    # Feedback on yesterday's picks: only the four feedback cells of rows that
    # are unambiguously identified; anything questionable is refused and reported.
    if slack_bot_token and slack_channel_id:
        try:
            # Exact cell text: numericised records would turn ts 1700000001.000100 into 1700000001.0001.
            fetched = fetch_feedback(slack_bot_token, slack_channel_id, [r.raw for r in suggestions_table.rows], yesterday)
            fb = apply_feedback(gc, sheet_id, suggestions_table, fetched)
            runtime.note("feedback", {"updated": len(fb.updated), "unchanged": len(fb.unchanged),
                                      "moved": fb.moved, "refused": fb.refused})
            if fb.refused:
                logger.warning("Feedback refused for %d suggestion(s): %s", len(fb.refused), fb.refused)
        except (runtime.WriteBlocked, SuggestionsWriteIncident):
            raise
        except Exception as exc:
            logger.warning("Feedback collection failed (non-fatal): %s", exc)
    slack_target = SlackTarget(slack_bot_token, slack_channel_id) if slack_bot_token and slack_channel_id else None

    # Step 1c: Resolve open delivery outcomes from Slack history (log -> post ->
    # confirm). A failed history read resolves nothing: those picks stay open,
    # keep suppressing, and are never reposted on that evidence.
    reconciliation = None
    if slack_target:
        try:
            reconciliation = reconcile(gc, sheet_id, suggestions_table, slack_target, today, datetime.now(timezone.utc))
            runtime.note("delivery_reconciliation", reconciliation.summary())
            if not reconciliation.history_ok:
                logger.warning("Delivery reconciliation unavailable: %s", reconciliation.history_error)
            suggestions_table = read_suggestions_table(gc, sheet_id)
        except (runtime.WriteBlocked, SuggestionsWriteIncident):
            raise
        except (SheetReadError, SuggestionsReadError) as exc:
            _stop(f"suggestions re-read failed after reconciliation: {exc}", "cannot read the suggestions tab — stopped")
            return
    if not slack_target:
        logger.info("SLACK_BOT_TOKEN or SLACK_CHANNEL_ID not set — skipping feedback and delivery reconciliation")

    # Step 2: Load state tab — detect cold start (validated above). An empty
    # state is only seeded when explicitly requested.
    state_rows = store.records
    cold_start = len(state_rows) == 0
    if cold_start and not runtime.env_flag("FARAMIR_COLD_START"):
        store.release_lock()
        ctx.halt("state tab is empty; seeding requires FARAMIR_COLD_START=true")
        post_alert(slack_url, f"Faramir {today}: state is empty — not seeding without FARAMIR_COLD_START", [])
        return
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
    runtime.note("source_dates", {"daily_chart": str(bom_date_used), "daily_rows": len(daily)})

    if is_monday:
        iso = yesterday.isocalendar()
        weekend = fetch_weekend_list(iso.year, iso.week)
        # Not weekend data: the legacy fetch returns Sunday's daily chart (see its docstring).
        logger.info("Monday: %d entries from the Sunday daily chart (legacy weekend fetch)", len(weekend))
        runtime.note("monday_merge", {"source": "sunday_daily_chart_via_legacy_fetch_weekend_list",
                                      "is_weekend_data": False, "entries": len(weekend)})
        # Merge weekend entries for any titles not already in daily
        daily_titles = {normalize_title(e["title"]) for e in daily}
        for w in weekend:
            if normalize_title(w["title"]) not in daily_titles:
                daily.append(w)

    # Step 4: Resolve each chart row's identity, then update state. A title
    # match alone never establishes identity (faramir.identity): stored TN
    # slug, editor overrides, year-aware search, re-release detection, and
    # unresolved films are skipped and reported, never guessed.
    new_state: dict[int, dict] = {}
    overrides = _identity_overrides(gc, sheet_id)
    state_list = list(state_by_tmdb.values())
    resolved: list[tuple[dict, "identity_mod.Identity"]] = []
    long_running: list[dict] = []  # re-release evidence, report only (SR-5); never used for picks
    for entry in daily:
        days = int(entry.get("days_in_release") or 0)
        slug, slug_year = identity_mod.parse_slug(f"/movie/{entry['tn_slug']}" if entry.get("tn_slug") else "")
        if days > config.MAX_DAYS_IN_RELEASE:
            kind = identity_mod.classify_engagement(slug_year, days, bom_date_used)
            if kind == "rerelease":
                long_running.append({"title": entry["title"], "tn_slug": slug, "slug_year": slug_year,
                                     "days_in_release": days, "total_gross": entry.get("gross_to_date"),
                                     "total_gross_scope": "all_engagements"})
            continue
        if int(entry.get("gross_to_date") or 0) < config.ACTIVE_FLOOR_USD:
            continue
        chart_entry = identity_mod.ChartEntry(entry["title"], slug, slug_year, days, bom_date_used)
        ident = identity_mod.resolve(chart_entry, state_list, overrides, search_movie, get_movie_details,
                                     get_release_dates)
        resolved.append((entry, ident))
    identity_mod.dedupe([i for _, i in resolved])
    runtime.note("identity", [i.report() for _, i in resolved])
    runtime.note("identity_overrides", {"used": sorted(k for k, v in overrides.by_slug.items() if isinstance(v, int)),
                                        "problems": overrides.problems})
    runtime.note("rerelease_engagements", long_running)
    unresolved = [i for _, i in resolved if i.status == "unresolved"]
    identity_warning = None
    if unresolved:
        names = ", ".join(f"{i.title} ({i.reason.split(':')[0]})" for i in unresolved[:5])
        more = f" and {len(unresolved) - 5} more" if len(unresolved) > 5 else ""
        identity_warning = (f"⚠ Film identity unresolved — not tracked today: {names}{more}. "
                            "Set its TMDB id in identity_overrides to include it.")

    consumed: set[int] = set()  # state rows continued (or re-keyed) by today's chart
    for entry, ident in resolved:
        bom_title = entry["title"]
        days = int(entry.get("days_in_release") or 0)
        gross_today = int(entry.get("gross_to_date") or 0)
        if ident.status != "resolved":
            logger.warning("Identity %s for '%s' (%s): %s", ident.status, bom_title, ident.tn_slug or "no slug", ident.reason)
            continue
        tmdb_id = int(ident.tmdb_id)
        prior = ident.state_row
        if prior is not None:
            consumed.add(int(prior["tmdb_id"]))
        identity_cols = {"tn_slug": ident.tn_slug or (prior or {}).get("tn_slug", ""),
                         "identity_confidence": ident.confidence, "identity_method": ident.method}

        if prior is not None and int(prior["tmdb_id"]) == tmdb_id:
            row = dict(prior)
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
            # A same-day rerun keeps the previous DAY's figure as "yesterday" (rolling it to the
            # previous run's value would erase every crossing and the pace on a recovery rerun).
            rerun_today = str(row.get("last_seen")) == str(today)
            row["_rerun_today"] = rerun_today  # not a state column; read by step 5
            if not rerun_today:
                row["domestic_yesterday"] = prior_dom
                row["dom_stale_days"] = prior_stale + 1 if gross_today == prior_dom else 0
            elif gross_today != prior_dom:
                row["dom_stale_days"] = 0
            row["domestic_today"] = gross_today
            row["days_in_release"] = days
            row["last_seen"] = str(today)
            row["status"] = "active"
            row["bom_title_seen"] = bom_title
            row.update(identity_cols)
        else:
            details = ident.details or {}
            if prior is not None:
                # Re-key by an editor override: the chart's domestic history is
                # this film's; worldwide came from the old (wrong) IMDb ID.
                logger.info("'%s': identity override %s -> %s", bom_title, prior["tmdb_id"], tmdb_id)
            else:
                logger.info("New film: '%s' -> TMDB %s (%s, %s)", bom_title, tmdb_id, ident.method, ident.confidence)
            row = {
                "tmdb_id": tmdb_id,
                "imdb_id": details.get("imdb_id", ""),
                "canonical_title": details.get("title", bom_title),
                "bom_title_seen": bom_title,
                "release_date": details.get("release_date", "") or details.get("release_year", ""),
                "first_seen": (prior or {}).get("first_seen") or str(today),
                "last_seen": str(today),
                "status": "active",
                "domestic_yesterday": int((prior or {}).get("domestic_today") or 0),
                "domestic_today": max(gross_today, int((prior or {}).get("domestic_today") or 0)),
                "worldwide_yesterday": 0,
                "worldwide_today": 0,
                "days_in_release": days,
                "tmdb_id_override": (prior or {}).get("tmdb_id_override", ""),
                "notes": (prior or {}).get("notes", ""),
                "dom_stale_days": 0,
                "ww_stale_days": 0,
                **identity_cols,
            }

        new_state[tmdb_id] = row

    # Films in old state but not seen today: mark retired if movement stalled
    for tmdb_id, row in state_by_tmdb.items():
        if tmdb_id in new_state or tmdb_id in consumed:
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
            if not row.get("_rerun_today"):
                row["worldwide_yesterday"] = prior_ww
                row["ww_stale_days"] = prior_ww_stale + 1 if new_ww == prior_ww else 0
            elif new_ww != prior_ww:
                row["ww_stale_days"] = 0
            row["worldwide_today"] = new_ww
        elif not row.get("_rerun_today"):
            row["worldwide_yesterday"] = prior_ww
            row["ww_stale_days"] = prior_ww_stale + 1
            # Leave worldwide_today unchanged from state

    runtime.note("films", {
        "active": sum(1 for r in new_state.values() if r.get("status") == "active"),
        "tracked": len(new_state),
    })
    if cold_start:
        logger.info("Cold start detected — seeding state, skipping picks")
        store.write(list(new_state.values()))
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

    # Step 7: Apply stale-gross evidence (today's state and chart) to the
    # corpus checked in preflight. Only usable records are used from here on.
    corpus_load.apply_stale(list(new_state.values()), daily, bom_date_used)
    runtime.note("corpus", corpus_load.summary())
    runtime.note("corpus_exclusions", [vars(e) for e in corpus_load.exclusions])
    logger.info("Corpus health: %s", corpus_load.summary())
    corpus = corpus_load.films

    # Step 8: Audience signals. Phase 2: from the sanitized Cerebro snapshot when configured
    # (FARAMIR_CEREBRO_SNAPSHOT; the established contract — News pool, matured, cumulative
    # sessions per article); otherwise the legacy live-sheet signals.
    from faramir.analytics import audience as audience_mod, cerebro_snapshot

    cerebro_snap = None
    try:
        cerebro_snap = cerebro_snapshot.configured()
    except Exception as exc:  # a refused or unreadable snapshot: no snapshot signals, never a guess
        logger.warning("Cerebro snapshot not used: %s", exc)
    cerebro_rows = []
    if cerebro_sheet_id and cerebro_snap is None:
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

    if cerebro_snap is not None:
        cerebro_signals = audience_mod.signals(cerebro_snap, list(set(all_talent_names + all_collection_names)))
        runtime.note("cerebro_source", {"source": "snapshot", "as_of": cerebro_snap.as_of.isoformat(),
                                        "as_of_basis": cerebro_snap.as_of_basis, "articles": len(cerebro_snap.articles)})
    else:
        talent_signals = build_talent_signals(cerebro_rows, list(set(all_talent_names)))
        franchise_signals = build_franchise_signals(cerebro_rows, list(set(all_collection_names)))
        cerebro_signals = {**talent_signals, **franchise_signals}
        runtime.note("cerebro_source", {"source": "live_sheet" if cerebro_rows else "none"})

    # Step 9: Build suppressed pairs from recent suggestions
    # Definitely-undelivered picks (not_delivered/expired/failed/dismissed) no longer suppress.
    suppressed_pairs = _suppressed_pairs(suppression_records(suggestions_table), today, config.SUPPRESS_DAYS)
    runtime.note("suppressed_pairs", len(suppressed_pairs))

    # Step 10: Find candidates and milestones
    candidates = find_all_candidates(enriched_active, corpus, cerebro_signals, suppressed_pairs)
    logger.info("Matcher: %d total candidates across all active films", len(candidates))
    runtime.note("candidates", len(candidates))

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

    # Step 10b: Strategy engine (Phase 1). Deterministic detectors (Overtake, Chase,
    # Countdown) turn candidates into evidence-backed events; the classifier keeps one
    # primary (and at most one secondary) per cluster; dedup drops what was already
    # delivered. Claude then chooses only among these. Every candidate and event gets a
    # ledger row with its disposition.
    from faramir import editorial, strategy_engine
    from faramir.events.ledger import default_ledger

    ledger = default_ledger(ctx.out_dir, dry_run=ctx.dry_run)
    ledger.append("editorial", editorial.capture([r.raw for r in suggestions_table.rows], today), month=str(today)[:7])
    engine = strategy_engine.run_daily(enriched_active, active_films_payload, corpus, bom_date_used.isoformat(),
                                       corpus_load.meta.get("gross_as_of"), suppressed_pairs, ledger,
                                       tracked=list(new_state.values()))
    active_films_payload = engine.payload_films
    runtime.note("strategy_engine", engine.summary())

    # Phase 2: headline guidance per event — constructions that fit the strategy, ranked by
    # MovieWeb's historical box-office results (when a snapshot exists), plus proven MW Power Words.
    from faramir.analytics import power_words as power_words_mod
    from faramir.headlines.analysis import analyze as analyze_headlines
    from faramir.headlines.recommend import recommend

    headline_analysis = analyze_headlines(cerebro_snap) if cerebro_snap is not None else None
    words = power_words_mod.configured()
    headline_recs = {}
    for pf in active_films_payload:
        for cand in pf["candidates"]:
            rec = recommend(cand.get("strategy", ""), headline_analysis, words)
            cand["headline_guidance"] = rec.guidance()
            headline_recs[cand.get("event_id")] = rec
    runtime.note("headline_intelligence", {
        "snapshot": headline_analysis is not None, "power_words": len(words),
        "box_office_articles": headline_analysis.articles if headline_analysis else 0,
        "baseline": headline_analysis.baseline if headline_analysis else None})

    # Phase 1b: weekend strategies (Takeover, Hold) on the latest completed weekend, from committed
    # weekend history only. With collection off (PR-16) this records that there is no data.
    from faramir.strategies import weekend_runner
    from faramir.tn_chart import weekend_friday

    latest_weekend = weekend_friday(today - timedelta(days=3)).isoformat()
    try:
        weekend_result = weekend_runner.run(gc, sheet_id, latest_weekend, ledger)
    except Exception as exc:  # history unreadable: no weekend events, never a guess
        weekend_result = weekend_runner.WeekendResult("history_unavailable", latest_weekend, detail=str(exc)[:200])
    runtime.note("weekend_strategies", weekend_result.summary())

    # Phase 3: Discovery over the same committed weekend history (off with collection, PR-16).
    from faramir.discovery import runner as discovery_runner

    try:
        discovery_result = discovery_runner.run(gc, sheet_id, latest_weekend, ledger, weekend_result.events)
    except Exception as exc:
        discovery_result = discovery_runner.DiscoveryResult("history_unavailable", latest_weekend)
        logger.warning("Discovery skipped: %s", exc)
    runtime.note("discovery", discovery_result.summary())

    # Step 10c (Phase 4): learning and prioritization. Published articles are linked and observed
    # in today's snapshot (age-comparable outcomes); estimates shrink toward broader levels and
    # stay "calibration pending" without outcomes; every event — daily, weekend, Discovery — gets
    # a scored decision record; only the ranked set (plus exploration) reaches Claude.
    from faramir import prioritize as prio
    from faramir.editorial import match_published
    from faramir.learning import outcomes as outcomes_mod, pipeline as learn_pipeline, priority as priority_mod
    from faramir.learning.estimates import estimate as estimate_outcomes

    priority_cfg = priority_mod.load()
    if cerebro_snap is not None:
        linked = [m for m in match_published([r.raw for r in suggestions_table.rows], cerebro_snap.articles)
                  if m.get("url") and not m.get("ambiguous")]
        rows_by_sid = {r.raw.get("suggestion_id"): r.raw for r in suggestions_table.rows}
        ledger.append("editorial", [{
            "id": f"published|{m['suggestion_id']}|{m['url']}", "suggestion_id": m["suggestion_id"], "status": "published",
            "reason_codes": [m["method"]], "published_url": m["url"], "observed": str(today),
            "headline": (rows_by_sid.get(m["suggestion_id"]) or {}).get("headline"), "match_confidence": m["confidence"]}
            for m in linked], month=str(today)[:7])
        ledger.append("outcome_observations", outcomes_mod.observe(cerebro_snap, {m["url"] for m in linked}),
                      month=str(today)[:7])
    outcome_rows, outcome_pending = learn_pipeline.outcome_rows(ledger, priority_cfg)
    estimates = estimate_outcomes(outcome_rows, "strategy", k=priority_cfg["shrinkage_k"],
                                  min_n=priority_cfg["experimental_below_n_eff"])
    delivered_ids = ledger.ids("deliveries")
    weekend_clusters = [c for c in weekend_result.clusters if c.primary.event_id not in delivered_ids]
    discovery_items = [i for i in discovery_result.items if i.id not in delivered_ids]
    slots = config.PICKS_MAX_BY_DAY.get(today.weekday(), 4) + 2
    active_films_payload, decisions = prio.prioritize(
        active_films_payload, engine.delivered_clusters, weekend_clusters, discovery_items,
        {f.get("tmdb_id"): f for f in enriched_active}, cerebro_signals, estimates,
        learn_pipeline.deliveries_by_strategy(ledger), headline_recs, priority_cfg, ctx.run_id, slots,
        learn_pipeline.explored_recently(ledger, str(today)))
    ledger.append("decisions", [d.record() for d in decisions], month=str(today)[:7])
    runtime.note("prioritization", {
        "config_version": priority_cfg["version"], "calibration": estimates["__global__"].status,
        "outcomes": len(outcome_rows), "outcomes_pending": outcome_pending, "decisions": len(decisions),
        "suggested": [f"{d.rank}:{d.strategy_primary}:{d.score}{' (exploration)' if d.exploration['flag'] else ''}"
                      for d in decisions if d.disposition["value"] == "suggested"]})

    # Step 11: Claude selection
    dow = today.weekday()
    picks_target = config.PICKS_TARGET.get(dow, 4)
    picks_min = config.PICKS_MIN_BY_DAY.get(dow, 2)
    picks_max = config.PICKS_MAX_BY_DAY.get(dow, 4)

    picks: list[dict] = []
    selection_warning = None
    if active_films_payload and any(f["candidates"] for f in active_films_payload):
        payload = build_payload(today, active_films_payload, picks_target, picks_min, picks_max)
        selection = select(payload)
        runtime.note("claude_selection", selection.summary())
        if selection.status != "ok":
            # Fail safe: no picks, never a guess or a stale recommendation.
            logger.error("Claude selection failed (%s): %s", selection.status, selection.reason)
            selection_warning = f"⚠ Pick selection unavailable today ({selection.reason or selection.status}) — no picks posted."
        # Every pick must bind to a real candidate; each gets a candidate_id and evidence snapshot.
        binding = bind_picks(selection.picks, active_films_payload, bom_date_used.isoformat(),
                             corpus_load.meta.get("gross_as_of"))
        runtime.note("claude_binding", binding.summary())
        for r in binding.rejected:
            logger.warning("Rejected Claude pick %s: %s", r["pick"].get("headline"), r["reason"])
        picks = binding.accepted
    else:
        logger.warning("No candidates to send to Claude today")

    # Step 11b: Validate every bound pick's words against its evidence: declared
    # claims, rank/superlative/tense/genre/third-film scan, every dollar figure,
    # and the 85-character headline (one shortening attempt, re-validated and
    # checked for unchanged meaning, never truncated).
    from faramir.claude_select import shorten_headline
    from faramir.validate import HEADLINE_MAX, meaning_changes, title_lexicon, validate_pick

    lexicon = title_lexicon(corpus, active_films_payload)
    validated_picks, validation_log = [], []
    for pick in picks:
        result = validate_pick(pick, lexicon)
        entry = {"candidate_id": pick.get("candidate_id"), "headline": pick.get("headline"), "reasons": result.reasons}
        if result.only_length:
            new_headline, record = shorten_headline(pick, HEADLINE_MAX)
            entry["shorten"] = record
            if new_headline:
                retry = dict(pick, headline=new_headline)
                result = validate_pick(retry, lexicon)
                result.reasons += meaning_changes(pick, new_headline)  # same films, direction, status, figures
                entry.update(headline_after=new_headline, reasons_after=result.reasons)
                if result.ok:
                    pick = retry
        entry["accepted"] = result.ok
        validation_log.append(entry)
        if not result.ok:
            logger.warning("Dropping pick '%s': %s", pick.get("headline", ""), "; ".join(result.reasons))
            continue
        validated_picks.append(pick)
    runtime.note("validation", validation_log)
    runtime.note("picks", {"returned": len(picks), "after_validation": len(validated_picks)})
    picks = validated_picks
    # Each pick carries its strategy event: id, strategy and the evidence-only rationale line.
    for pick in picks:
        cluster = engine.by_candidate_id.get(pick.get("candidate_id"))
        if cluster is not None:
            from faramir import rationale as rationale_mod

            pick["rationale"] = rationale_mod.line(cluster.primary, cluster.secondary)
            pick["evidence"] = {**(pick.get("evidence") or {}), "strategy_event": {
                "event_id": cluster.primary.event_id, "strategy": cluster.primary.strategy,
                "secondary": cluster.secondary.strategy if cluster.secondary else None,
                "rationale": pick["rationale"], "reason_codes": cluster.primary.reason_codes}}
            from faramir.headlines.taxonomy import tag as tag_construction

            rec = headline_recs.get(cluster.primary.event_id)
            used = tag_construction(pick.get("headline", ""))
            pick["evidence"]["headline"] = {
                "constructions": used,
                "followed_guidance": bool(rec and set(used) & set(rec.guidance()["preferred_constructions"])),
                "recommended": rec.guidance() if rec else None,
                "calibration": rec.calibration if rec else "no_evidence"}
    # Phase 4: the title refiner (Aragorn) behind its interface; NullRefiner until a real adapter is
    # approved. Any refined headline is used only if it re-validates (facts, meaning, 85 chars).
    from faramir.adapters.aragorn import NullRefiner, refine_and_validate

    refiner = NullRefiner()
    refined = []
    for pick in picks:
        pick, aragorn_record = refine_and_validate(pick, refiner, lexicon)
        pick["evidence"] = {**(pick.get("evidence") or {}), "aragorn": aragorn_record}
        if pick.get("kind") == "event":
            pick["rationale"] = ((pick["evidence"].get("strategy_event") or {}).get("rationale"))
        refined.append(pick)
    picks = refined
    corpus_by_tmdb = {f.get("tmdb_id"): f for f in corpus}

    # Step 12: Log -> post -> confirm. Every pick gets an immutable suggestion_id
    # and a delivery_state; nothing already logged is ever knowingly reposted.
    data_date = bom_date_used
    usable_comp_ids = {str(f.get("tmdb_id")) for f in corpus if f.get("tmdb_id") is not None}
    existing_ids = suggestions_table.by_id()

    new_rows: list[dict] = []
    new_items: list = []
    skipped_existing: list[str] = []
    for pick in picks:
        comp_tmdb = pick.get("comp_tmdb_id")
        comp_film = corpus_by_tmdb.get(comp_tmdb) if comp_tmdb else None
        active_tmdb = pick.get("active_tmdb_id")
        active_film_match = next((f for f in enriched_active if f.get("tmdb_id") == active_tmdb), {})
        row = {
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
            "slack_ts": "",
            "thumbs_up": "",
            "thumbs_down": "",
            "thread_replies": "",
            "thread_feedback": "",
            "delivery_state": "planned",
            "delivery_attempts": "0",
            "last_attempt_at": "",
            "delivery_run_id": ctx.run_id,
            "data_date": data_date.isoformat(),
            "delivery_note": "",
            "candidate_id": pick.get("candidate_id", ""),
            "evidence_json": json.dumps(pick.get("evidence"), sort_keys=True, separators=(",", ":"), default=str),
        }
        sid = derive_suggestion_id(row)
        if sid is None:
            logger.warning("Pick without identifying fields skipped: %s", pick.get("headline"))
            continue
        if sid in existing_ids:
            skipped_existing.append(sid)  # already logged (posted, open or undelivered): never knowingly repost
            continue
        row["suggestion_id"] = sid
        new_rows.append(row)
        new_items.append(DeliveryItem(sid, pick, active_film_match, comp_film))

    # Undelivered picks: automatic retry only when Slack history was read
    # reliably this run and the evidence is unchanged; the rest expire for
    # manual review (scripts/replay_picks.py).
    triage = triage_undelivered(suggestions_table, today, data_date, usable_comp_ids)
    if triage.expired:
        update_by_id(gc, sheet_id, {sid: {"delivery_state": "expired", "delivery_note": f"expired: {why}"}
                                    for sid, why in triage.expired.items()}, "expire_delivery")
    replay_items = []
    if slack_target and reconciliation is not None and reconciliation.history_ok:
        for r in triage.replay:
            rec = r.record
            comp_id = rec.get("comp_tmdb_id")
            try:
                comp_film = corpus_by_tmdb.get(int(comp_id)) if comp_id not in ("", None, "None") else None
            except (TypeError, ValueError):
                comp_film = None
            active_film_match = next((f for f in enriched_active if str(f.get("tmdb_id")) == str(rec.get("active_tmdb_id"))), {})
            replay_items.append(DeliveryItem(r.sid, dict(rec), active_film_match, comp_film,
                                             attempts_before=int(rec.get("delivery_attempts") or 0), replay=True))

    # Daily cap across reruns: already-logged picks for today count against it.
    already_today = sum(1 for r in suggestions_table.rows
                        if str(r.get("run_date")) == today.isoformat() and r.get("delivery_state") in COUNTS_TOWARD_TODAY)
    capacity = max(0, picks_max - already_today)
    replay_items = replay_items[:capacity]
    capacity -= len(replay_items)
    over_cap = [i.suggestion_id for i in new_items[capacity:]]
    new_items = new_items[:capacity]
    keep = {i.suggestion_id for i in new_items}
    new_rows = [r for r in new_rows if r["suggestion_id"] in keep]

    # Step 13a: log new picks as 'planned' BEFORE any Slack post.
    if new_rows:
        appended = append_new(gc, sheet_id, new_rows)
        runtime.note("suggestions_append", {
            "appended": len(appended.appended), "already_present": len(appended.already_present),
            "attempts": appended.attempts, "duplicates_after": appended.duplicates_after,
        })

    deliveries = replay_items + new_items
    post_hdr, header_why = should_post_header(reconciliation, slack_target is not None)
    if post_hdr:
        if slack_target:
            post_header_message(slack_bot_token, slack_channel_id, len(deliveries), len(enriched_active), today,
                                warning=_join_warnings(corpus_load.editor_warning(), identity_warning, selection_warning))
        else:
            post_header(slack_url, len(deliveries), len(enriched_active), today, warning=_join_warnings(corpus_load.editor_warning(), identity_warning, selection_warning))

    outcomes = []
    if deliveries and slack_target:
        outcomes = deliver(gc, sheet_id, slack_target, deliveries, ctx.run_id)
    elif deliveries:
        logger.warning("No SLACK_BOT_TOKEN/SLACK_CHANNEL_ID — %d pick(s) logged as planned, not posted", len(deliveries))
    # Delivered events feed cross-day dedup (and later, learning).
    posted = {o.suggestion_id for o in outcomes if o.state == "posted"}
    delivered_clusters, sid_by_event = [], {}
    for item in new_items:
        cluster = engine.by_candidate_id.get(item.pick.get("candidate_id"))
        if cluster is not None and item.suggestion_id in posted:
            delivered_clusters.append(cluster)
            sid_by_event[cluster.primary.event_id] = item.suggestion_id
    weekend_by_event = {c.primary.event_id: c for c in weekend_result.clusters}
    for item in new_items:
        se = (item.pick.get("evidence") or {}).get("strategy_event") or {}
        if item.pick.get("kind") == "event" and item.suggestion_id in posted:
            if se.get("event_id") in weekend_by_event:
                delivered_clusters.append(weekend_by_event[se["event_id"]])
                sid_by_event[se["event_id"]] = item.suggestion_id
            elif se.get("strategy") == "discovery":
                ledger.append("deliveries", [{"id": se["event_id"], "event_id": se["event_id"], "story_key": se["event_id"],
                                              "strategy": "discovery", "status": "proposed", "period": str(today),
                                              "suggestion_id": item.suggestion_id, "evidence": se.get("values")}])
    strategy_engine.record_deliveries(ledger, delivered_clusters, bom_date_used.isoformat(), sid_by_event)
    runtime.note("delivery", {
        "new": [i.suggestion_id for i in new_items], "replayed": [i.suggestion_id for i in replay_items],
        "skipped_already_logged": skipped_existing, "over_daily_cap": over_cap,
        "expired": triage.expired, "header": header_why,
        "outcomes": [o.__dict__ for o in outcomes],
    })

    # Step 13b: write state.
    runtime.note("state_generation", store.write(list(new_state.values())))

    # Step 14: Flush Claude usage records to the Usage sheet tab (fails open)
    runtime.note("claude_usage", list(sheet_module._USAGE_LOG))
    flush_usage_to_sheet(gc, sheet_id)

    logger.info("=== Faramir complete: %d picks logged ===", len(picks))


def main(argv: list[str] | None = None) -> int:
    """Entry point. Live runs need FARAMIR_ENABLED=true (fail-closed);
    ``--dry-run`` records every write under out/<run_id>/ instead; and
    ``--fixtures DIR`` (dry-run only) runs fully offline from fixture files.
    """
    import argparse

    from faramir import runtime

    parser = argparse.ArgumentParser(prog="run_daily.py")
    parser.add_argument("--dry-run", action="store_true", default=runtime.env_flag("FARAMIR_DRY_RUN"))
    parser.add_argument("--fixtures", default=None, help="offline fixture directory (requires --dry-run)")
    parser.add_argument("--out-root", default=None, help="where run artifacts go (default: out/)")
    parser.add_argument("--run-date", default=None, help="YYYY-MM-DD, dry-run only")
    args = parser.parse_args(argv or [])

    ctx = runtime.new_context(dry_run=args.dry_run, fixtures_dir=args.fixtures, out_root=args.out_root)
    ok, why = ctx.may_run()
    if not ok:
        logger.warning("Faramir not started: %s", why)
        ctx.report["not_started"] = why
        ctx.write_artifacts()
        return 0
    if args.run_date and not ctx.dry_run:
        logger.error("--run-date is only allowed with --dry-run")
        return 2
    run_date = date.fromisoformat(args.run_date) if args.run_date else None

    rc = 0
    with runtime.use(ctx):
        try:
            if ctx.fixtures_dir:
                from faramir import offline

                with offline.activate(ctx.fixtures_dir):
                    _run(ctx, run_date or offline.run_date())
            else:
                _run(ctx, run_date)
        except Exception as exc:
            rc = 1
            logger.exception("Faramir daily run failed with unhandled exception")
            runtime.note("error", f"{type(exc).__name__}: {exc}")
            slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
            if slack_url:
                try:
                    from faramir.slack_post import post_failure

                    post_failure(slack_url, exc, os.environ.get("GITHUB_WORKFLOW_URL", ""))
                except Exception:
                    logger.exception("Could not send failure alert")
        finally:
            for cleanup in ctx.on_exit:
                cleanup()
            ctx.write_artifacts()
            logger.info("Run artifacts: %s (writes: %s)", ctx.out_dir, ctx.counts())
    if ctx.halted_reason and rc == 0:
        rc = 3
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
