from __future__ import annotations
"""Slack webhook posting for Faramir using Block Kit.

post_pick() returns the Slack message timestamp (ts) so it can be stored
in the suggestions sheet and looked up later for reaction/feedback reading.
Webhook posts don't return a ts — we use chat.postMessage via the Bot Token
for pick messages so we can capture it. Header, failure, and init messages
still use the webhook (no ts needed).
"""

import logging
from dataclasses import dataclass
from datetime import date

import requests

from faramir.runtime import authorize, current, record_slack

logger = logging.getLogger(__name__)

TIMEOUT = 10

PICK_EVENT = "faramir_pick"
HEADER_EVENT = "faramir_header"


@dataclass
class PostResult:
    """Outcome of one chat.postMessage call.

    posted   Slack returned ok:true with a ts.
    failed   Slack returned ok:false (or HTTP 429): definitely not posted.
    unknown  timeout / connection error / unreadable response — Slack may or
             may not have posted it. Never treated as "not posted".
    dry_run  recorded locally, nothing sent.
    """

    status: str
    ts: str | None = None
    error: str | None = None


def header_ref(run_date: date) -> str:
    return f"daily-header-{run_date.isoformat()}"


def _post_webhook(webhook_url: str, blocks: list[dict], kind: str = "slack") -> None:
    """POST blocks to Slack via incoming webhook. No ts returned.

    ``kind`` is the write-guard category: "slack" for normal output, "alert"
    for failure alerts (the only Slack output allowed after a run halts).
    """
    allowed = authorize(kind, "webhook_post", {"blocks": len(blocks)})
    record_slack(f"webhook:{kind}", blocks, sent=allowed)
    if not allowed:
        return
    try:
        resp = requests.post(webhook_url, json={"blocks": blocks}, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Slack webhook post failed: %s", exc)


def _post_message(
    bot_token: str,
    channel_id: str,
    blocks: list[dict],
    metadata: dict | None = None,
    text: str | None = None,
) -> PostResult:
    """POST a message via chat.postMessage and classify the outcome (see PostResult).

    In dry-run the message is recorded locally and a placeholder ts is returned.
    """
    allowed = authorize("slack", "chat.postMessage", {"channel": channel_id, "blocks": len(blocks),
                                                      "metadata": metadata})
    record_slack("chat.postMessage", blocks, channel_id, sent=allowed)
    if not allowed:
        return PostResult("dry_run", current().next_dry_ts())
    payload = {"channel": channel_id, "blocks": blocks}
    if metadata:
        payload["metadata"] = metadata
    if text:
        payload["text"] = text
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=TIMEOUT,
        )
    except Exception as exc:  # timeout or dropped connection: Slack may have posted it
        logger.error("chat.postMessage outcome unknown: %s", exc)
        return PostResult("unknown", error=f"{type(exc).__name__}: {exc}")
    if getattr(resp, "status_code", 200) == 429:
        return PostResult("failed", error="ratelimited (HTTP 429)")
    try:
        data = resp.json()
    except Exception as exc:
        logger.error("chat.postMessage unreadable response: %s", exc)
        return PostResult("unknown", error=f"unreadable response (HTTP {getattr(resp, 'status_code', '?')})")
    if data.get("ok") and data.get("ts"):
        return PostResult("posted", data["ts"])
    if data.get("ok"):
        return PostResult("unknown", error="ok without ts")
    logger.error("chat.postMessage error: %s", data.get("error"))
    return PostResult("failed", error=str(data.get("error")))


def post_header(
    webhook_url: str,
    picks_count: int,
    active_films_count: int,
    run_date: date,
    warning: str | None = None,
) -> None:
    """Post the daily header summary message via webhook (no bot token).

    ``warning`` adds one context line (e.g. degraded comparison data) above the
    divider; normal days pass None and the header is unchanged.
    """
    _post_webhook(webhook_url, _header_blocks(picks_count, active_films_count, run_date, warning))


def post_header_message(
    bot_token: str,
    channel_id: str,
    picks_count: int,
    active_films_count: int,
    run_date: date,
    warning: str | None = None,
) -> PostResult:
    """The daily header via chat.postMessage, tagged so a rerun can find it.

    Incoming webhooks are not documented to carry message metadata, so the
    header moves to the bot when a bot token is configured.
    """
    blocks = _header_blocks(picks_count, active_films_count, run_date, warning)
    blocks.insert(-1, {"type": "context", "elements": [{"type": "mrkdwn", "text": f"ref {header_ref(run_date)}"}]})
    return _post_message(bot_token, channel_id, blocks,
                         metadata={"event_type": HEADER_EVENT, "event_payload": {"run_date": run_date.isoformat()}},
                         text=f"Faramir Daily — {run_date.isoformat()}")


def _header_blocks(picks_count: int, active_films_count: int, run_date: date, warning: str | None) -> list[dict]:
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🏹 Faramir Daily — {run_date.strftime('%A, %B %-d, %Y')}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{picks_count} editorial pick{'s' if picks_count != 1 else ''}* across "
                    f"*{active_films_count} active film{'s' if active_films_count != 1 else ''}* today."
                ),
            },
        },
    ]
    if warning:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": warning}]})
    blocks.append({"type": "divider"})
    return blocks


def post_pick(
    bot_token: str,
    channel_id: str,
    pick: dict,
    active_film: dict,
    comp_film: dict | None,
    suggestion_id: str | None = None,
    run_id: str | None = None,
    delayed_from: str | None = None,
) -> PostResult:
    """Post one pick via chat.postMessage.

    With a suggestion_id the message carries Slack metadata
    (event_type faramir_pick, payload {suggestion_id, run_id}) AND a visible
    "ref <id>" in its context line, so delivery can be reconciled from channel
    history even if the token cannot read metadata.
    """

    def fmt_money(val):
        if val is None:  # unavailable in the source — never shown as zero
            return "n/a"
        if not val:
            return "—"
        val = int(val)
        if val >= 1_000_000_000:
            return f"${val / 1_000_000_000:.2f}B"
        if val >= 1_000_000:
            return f"${val / 1_000_000:.1f}M"
        return f"${val:,}"

    headline = pick.get("headline", "")
    angle = pick.get("angle", "")
    axis = pick.get("axis", "domestic")
    kind = pick.get("kind", "")
    category = pick.get("angle_category", "")
    threshold = pick.get("threshold_value")
    active_title = pick.get("active_title") or active_film.get("canonical_title", "")
    comp_title = pick.get("comp_title") or (comp_film.get("title") if comp_film else "—")
    comp_year = comp_film.get("release_year", "") if comp_film else ""

    # Active film grosses
    active_dom = fmt_money(active_film.get("domestic_today"))
    active_ww = fmt_money(active_film.get("worldwide_today"))

    # Meta tag line: category · kind · axis
    kind_label = kind.replace("_", " ").title() if kind != "milestone" else "Milestone"
    category_label = category.replace("_", " ").title()
    axis_label = axis.capitalize()
    meta = f"{category_label}  ·  {kind_label}  ·  {axis_label}"
    if suggestion_id:
        meta += f"  ·  ref {suggestion_id}"

    if kind == "event":
        se = (pick.get("evidence") or {}).get("strategy_event") or {}
        numbers_block = {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"*{active_title}*  ·  {str(se.get('strategy', '')).replace('_', ' ').title()} event"
            + (f"  ·  No. {se['chart_rank']} on the weekend chart" if se.get("chart_rank") else ""))}}
    elif kind == "milestone":
        # Milestone pick: no comp film
        numbers_block = {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*{active_title}*\nDom: {active_dom}  ·  WW: {active_ww}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Milestone crossed*\n{fmt_money(threshold)} {axis_label}",
                },
            ],
        }
    elif comp_film:
        comp_dom = fmt_money(comp_film.get("domestic_lifetime"))
        comp_ww = fmt_money(comp_film.get("worldwide_lifetime"))
        comp_label = f"{comp_title} ({comp_year})" if comp_year else comp_title
        numbers_block = {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*{active_title}* (now)\nDom: {active_dom}  ·  WW: {active_ww}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*{comp_label}* (lifetime)\nDom: {comp_dom}  ·  WW: {comp_ww}",
                },
            ],
        }
    else:
        numbers_block = {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{active_title}*  Dom: {active_dom}  ·  WW: {active_ww}",
            },
        }

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{headline}*"},
        },
        numbers_block,
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": meta}],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{angle}_"},
        },
    ]
    if pick.get("rationale"):
        # Phase 1: one evidence-only line saying why this is a story (built in code, never by the model).
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Why: {pick['rationale']}"}]})
    blocks.append({"type": "divider"})

    if delayed_from:
        blocks.insert(0, {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"_Delayed suggestion from {delayed_from} (replayed by an operator)_"}]})
    metadata = None
    if suggestion_id:
        metadata = {"event_type": PICK_EVENT, "event_payload": {"suggestion_id": suggestion_id, "run_id": run_id or ""}}
    return _post_message(bot_token, channel_id, blocks, metadata=metadata, text=headline or None)


def post_failure(webhook_url: str, error: Exception, workflow_url: str = "") -> None:
    """Post an error alert via webhook."""
    text = f":x: *Faramir failed* — `{type(error).__name__}: {str(error)[:200]}`"
    if workflow_url:
        text += f"\n<{workflow_url}|View workflow run>"
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}], kind="alert")


def post_init(webhook_url: str, film_count: int) -> None:
    """Post cold-start notice via webhook."""
    text = (
        f"🏹 *Faramir initialized* — {film_count} active film"
        f"{'s' if film_count != 1 else ''} seeded. First picks will run tomorrow."
    )
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}])


def post_corpus_abort(webhook_url: str, reasons: list[str], run_date: date) -> None:
    """Alert that the comparison corpus failed integrity and no picks were made."""
    lines = "\n".join(f"• {r}" for r in reasons[:5])
    text = (
        f":warning: *Faramir {run_date.isoformat()}: comparison data failed integrity — no picks today.*\n"
        f"{lines}"
    )
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}], kind="alert")


def post_alert(webhook_url: str, title: str, lines: list[str]) -> None:
    """A failure/recovery alert (write-guard category ``alert``)."""
    body = "\n".join(f"• {line}" for line in lines[:6])
    text = f":warning: *{title}*" + (f"\n{body}" if body else "")
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}], kind="alert")
