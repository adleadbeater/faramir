from __future__ import annotations
"""Slack webhook posting for Faramir using Block Kit.

post_pick() returns the Slack message timestamp (ts) so it can be stored
in the suggestions sheet and looked up later for reaction/feedback reading.
Webhook posts don't return a ts — we use chat.postMessage via the Bot Token
for pick messages so we can capture it. Header, failure, and init messages
still use the webhook (no ts needed).
"""

import logging
from datetime import date

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10


def _post_webhook(webhook_url: str, blocks: list[dict]) -> None:
    """POST blocks to Slack via incoming webhook. No ts returned."""
    try:
        resp = requests.post(webhook_url, json={"blocks": blocks}, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Slack webhook post failed: %s", exc)


def _post_message(bot_token: str, channel_id: str, blocks: list[dict]) -> str | None:
    """POST a message via chat.postMessage. Returns ts on success, None on failure."""
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
            json={"channel": channel_id, "blocks": blocks},
            timeout=TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error("chat.postMessage error: %s", data.get("error"))
            return None
        return data.get("ts")
    except Exception as exc:
        logger.error("chat.postMessage failed: %s", exc)
        return None


def post_header(webhook_url: str, picks_count: int, active_films_count: int, run_date: date) -> None:
    """Post the daily header summary message via webhook."""
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
        {"type": "divider"},
    ]
    _post_webhook(webhook_url, blocks)


def post_pick(
    bot_token: str,
    channel_id: str,
    pick: dict,
    active_film: dict,
    comp_film: dict | None,
) -> str | None:
    """Post one pick via chat.postMessage. Returns the Slack ts for feedback tracking."""

    def fmt_money(val):
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

    if threshold:
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
        {"type": "divider"},
    ]

    return _post_message(bot_token, channel_id, blocks)


def post_failure(webhook_url: str, error: Exception, workflow_url: str = "") -> None:
    """Post an error alert via webhook."""
    text = f":x: *Faramir failed* — `{type(error).__name__}: {str(error)[:200]}`"
    if workflow_url:
        text += f"\n<{workflow_url}|View workflow run>"
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}])


def post_init(webhook_url: str, film_count: int) -> None:
    """Post cold-start notice via webhook."""
    text = (
        f"🏹 *Faramir initialized* — {film_count} active film"
        f"{'s' if film_count != 1 else ''} seeded. First picks will run tomorrow."
    )
    _post_webhook(webhook_url, [{"type": "section", "text": {"type": "mrkdwn", "text": text}}])
