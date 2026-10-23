"""Slack delivery: log -> post -> confirm, reconciliation and replay rules.

Per-pick ``delivery_state`` on the suggestions tab (schema v2):

  planned        logged; no Slack attempt yet
  sending        recorded immediately before chat.postMessage
  posted         Slack returned a ts, or history shows the tagged message
  failed         Slack returned ok:false — definitely not posted
  unknown        timeout / unreadable response — Slack MAY have posted it
  not_delivered  never attempted (logged 'planned', no Slack call made) — safe to retry
  unconfirmed    an attempt was made and a reliable history read did not find
                 it — the outcome is still ambiguous, so it is NEVER retried
                 automatically; it keeps suppressing and is listed for review.
                 Every later complete history read looks for it again: if one
                 positively identifies the message (metadata or visible ref),
                 it becomes 'posted'; otherwise nothing changes
  expired        undelivered and no longer eligible for automatic retry
  dismissed      an operator chose not to deliver it
  legacy_unknown migrated pre-v2 row without a Slack ts

Guarantees (and what is NOT guaranteed):
* No pick is reposted while its outcome is open (planned/sending/unknown) or
  posted. An open outcome is resolved only by a successful, complete history
  read; a failed history read changes nothing.
* Automatic re-delivery happens only for picks that were never attempted
  (not_delivered) or that Slack definitively rejected (failed). An attempted
  pick that history does not show becomes 'unconfirmed' and needs an operator
  (scripts/replay_picks.py) — absence from a history response is never, by
  itself, a reason to post again. Duplicates found in history are reported.
* Not exactly-once: Slack has no idempotency key for chat.postMessage.
* Nothing is silently lost: an undelivered pick ends posted, expired (listed
  for manual review) or dismissed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from faramir import runtime
from faramir.slack_history import fetch_tagged
from faramir.slack_post import post_pick
from faramir.suggestions import Table, _norm, update_by_id

logger = logging.getLogger(__name__)

OPEN = {"planned", "sending", "unknown"}
RECHECK = {"unconfirmed"}  # ambiguous: confirmed from history if found; never reposted automatically
NON_SUPPRESSING = {"not_delivered", "expired", "failed", "dismissed"}
REPLAYABLE = {"not_delivered", "failed"}
COUNTS_TOWARD_TODAY = {"planned", "sending", "unknown", "posted"}
RECONCILE_WINDOW_DAYS = 7  # covers the 5-day suppression window
ATTEMPT_GRACE = timedelta(minutes=5)
MAX_AUTO_ATTEMPTS = 3


@dataclass
class SlackTarget:
    bot_token: str
    channel_id: str


def suppression_records(table: Table) -> list[dict]:
    """Rows that suppress a pair: everything except definitely-undelivered states."""
    return [r.record for r in table.rows if r.get("delivery_state") not in NON_SUPPRESSING]


def _parse_ts(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Reconciliation:
    history_ok: bool
    history_error: str | None = None
    header_posted_today: bool | None = None
    changed: dict[str, str] = field(default_factory=dict)
    still_open: list[str] = field(default_factory=list)
    duplicate_posts: dict[str, list[str]] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)
    pages: int = 0
    matched_by_metadata: int = 0
    matched_by_ref: int = 0

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def reconcile(client, sheet_id: str, table: Table, slack: SlackTarget, run_date: date, now: datetime) -> Reconciliation:
    """Resolve open outcomes from Slack history. A failed read resolves nothing."""
    oldest = datetime.combine(run_date - timedelta(days=RECONCILE_WINDOW_DAYS), time.min, tzinfo=timezone.utc)
    window_start = (run_date - timedelta(days=RECONCILE_WINDOW_DAYS)).isoformat()
    open_rows = [r for r in table.rows if r.get("delivery_state") in OPEN]
    recheck_rows = [r for r in table.rows if r.get("delivery_state") in RECHECK]
    hist = fetch_tagged(slack.bot_token, slack.channel_id, oldest.timestamp())
    rec = Reconciliation(history_ok=hist.ok, history_error=hist.error, pages=hist.pages,
                         matched_by_metadata=hist.matched_by_metadata, matched_by_ref=hist.matched_by_ref)
    if not hist.ok:
        rec.still_open = [r.sid for r in open_rows]
        logger.warning("Slack history unavailable (%s): %d open delivery outcome(s) left unresolved",
                       hist.error, len(open_rows))
        return rec
    rec.header_posted_today = bool(hist.headers.get(run_date.isoformat()))
    rec.duplicate_posts = {sid: sorted(ts) for sid, ts in hist.picks.items() if len(ts) > 1}

    changes: dict[str, dict] = {}
    for row in open_rows:
        found = sorted(hist.picks.get(row.sid, []))
        if str(row.get("run_date")) < window_start:
            rec.still_open.append(row.sid)  # outside the history window: manual review
            continue
        if found:
            note = "confirmed from Slack history"
            if len(found) > 1:
                note += f"; DUPLICATE POSTS {found} — review"
            changes[row.sid] = {"delivery_state": "posted", "slack_ts": found[0], "delivery_note": note}
        elif row.get("delivery_state") == "planned":
            changes[row.sid] = {"delivery_state": "not_delivered", "delivery_note": "never attempted (run stopped before posting)"}
        else:
            last = _parse_ts(row.get("last_attempt_at"))
            if last is not None and now - last >= ATTEMPT_GRACE:
                # Attempted, outcome ambiguous: absence from history is not proof
                # it wasn't posted. Never retried automatically.
                changes[row.sid] = {"delivery_state": "unconfirmed",
                                    "delivery_note": f"attempted; not found in Slack history ({hist.pages} page(s)) "
                                                     "— check the channel, then replay or dismiss"}
            else:
                rec.still_open.append(row.sid)
    for row in recheck_rows:
        found = sorted(hist.picks.get(row.sid, []))
        if found and str(row.get("run_date")) >= window_start:
            note = "confirmed from Slack history after being unconfirmed"
            if len(found) > 1:
                note += f"; DUPLICATE POSTS {found} — review"
            changes[row.sid] = {"delivery_state": "posted", "slack_ts": found[0], "delivery_note": note}
        # Not found (or outside the window): stays unconfirmed — absence is not proof either way.
    if changes:
        res = update_by_id(client, sheet_id, changes, "reconcile_delivery")
        rec.refused = res.refused
        rec.changed = {sid: changes[sid]["delivery_state"] for sid in res.updated}
    return rec


@dataclass
class Triage:
    replay: list = field(default_factory=list)            # Row objects eligible for automatic re-delivery
    expired: dict[str, str] = field(default_factory=dict)  # suggestion_id -> reason


def triage_undelivered(table: Table, run_date: date, data_date: date, usable_comp_ids: set[str]) -> Triage:
    """Automatic retry only while the evidence is the same as when it was picked."""
    out = Triage()
    for row in table.rows:
        if row.get("delivery_state") not in REPLAYABLE:
            continue
        reasons = []
        if str(row.get("run_date")) != run_date.isoformat():
            reasons.append("picked on an earlier day")
        if row.get("data_date") != data_date.isoformat():
            reasons.append("box office data has changed since it was picked")
        try:
            attempts = int(row.get("delivery_attempts") or 0)
        except ValueError:
            attempts = MAX_AUTO_ATTEMPTS
        if attempts >= MAX_AUTO_ATTEMPTS:
            reasons.append(f"{attempts} attempts already")
        comp = _norm(row.get("comp_tmdb_id"))
        if comp and comp not in usable_comp_ids:
            reasons.append("comparison record is no longer usable")
        if reasons:
            out.expired[row.sid] = "; ".join(reasons)
        else:
            out.replay.append(row)
    return out


@dataclass
class DeliveryItem:
    suggestion_id: str
    pick: dict
    active_film: dict
    comp_film: dict | None
    attempts_before: int = 0
    replay: bool = False


@dataclass
class DeliveryOutcome:
    suggestion_id: str
    state: str
    ts: str | None = None
    error: str | None = None
    confirmed: bool = True


def deliver(client, sheet_id: str, slack: SlackTarget, items: list[DeliveryItem], run_id: str,
            now_fn=lambda: datetime.now(timezone.utc)) -> list[DeliveryOutcome]:
    """For each item: mark sending -> post -> confirm. Never posts an item whose
    'sending' mark could not be written."""
    outcomes = []
    for item in items:
        mark = update_by_id(client, sheet_id, {item.suggestion_id: {
            "delivery_state": "sending", "delivery_attempts": str(item.attempts_before + 1),
            "last_attempt_at": now_fn().isoformat(timespec="seconds"), "delivery_run_id": run_id}}, "mark_sending")
        if item.suggestion_id in mark.refused:
            outcomes.append(DeliveryOutcome(item.suggestion_id, "not_attempted", error=mark.refused[item.suggestion_id]))
            continue
        res = post_pick(slack.bot_token, slack.channel_id, item.pick, item.active_film, item.comp_film,
                        suggestion_id=item.suggestion_id, run_id=run_id)
        state = "posted" if res.status in ("posted", "dry_run") else res.status
        outcome = DeliveryOutcome(item.suggestion_id, state, res.ts, res.error)
        try:
            update_by_id(client, sheet_id, {item.suggestion_id: {
                "delivery_state": state, "slack_ts": res.ts or "", "delivery_note": res.error or ""}},
                "confirm_delivery")
        except runtime.WriteBlocked:
            raise
        except Exception as exc:  # row stays 'sending'; the next run reconciles it from Slack history
            outcome.confirmed = False
            logger.error("Could not confirm delivery of %s (%s); next run will reconcile", item.suggestion_id, exc)
        outcomes.append(outcome)
    return outcomes


def should_post_header(rec: Reconciliation | None, have_bot: bool) -> tuple[bool, str]:
    if not have_bot:
        return True, "webhook header (no bot token: cannot check for an earlier header)"
    if rec is None or not rec.history_ok:
        return False, "Slack history unavailable — header skipped to avoid a duplicate"
    if rec.header_posted_today:
        return False, "header already posted today"
    return True, "first header today"
