from __future__ import annotations
"""Slack webhook posting for Faramir using Block Kit."""

import json
import logging
from datetime import date

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10


def _post(webhook_url: str, blocks: list[dict]) -> None:
    """POST blocks payload to Slack webhook."""
    body = {"blocks": blocks}
    try:
        resp = requests.post(webhook_url, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Slack post failed: %s", exc)


def post_header(webhook_url: str, picks_count: int, active_films_count: int, run_date: date) -> None:
    """Post the daily header summary message."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":clapper: Faramir Daily — {run_date.strftime('%A, %B %-d, %Y')}",
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
    _post(webhook_url, blocks)


def post_pick(webhook_url: str, pick: dict, active_film: dict, comp_film: dict | None) -> None:
    """Post one pick as a Block Kit message."""
    headline = pick.get("headline", "")
    angle = pick.get("angle", "")
    axis = pick.get("axis", "")
    kind = pick.get("kind", "")
    category = pick.get("angle_category", "")
    threshold = pick.get("threshold_value")
    active_title = pick.get("active_title") or active_film.get("canonical_title", "")
    comp_title = pick.get("comp_title") or (comp_film.get("title") if comp_film else "N/A")

    # Format numbers
    def fmt_money(val):
        if val is None:
            return "N/A"
        if val >= 1_000_000_000:
            return f"${val/1_000_000_000:.2f}B"
        if val >= 1_000_000:
            return f"${val/1_000_000:.1f}M"
        return f"${val:,}"

    dom_today = fmt_money(active_film.get("domestic_today"))
    ww_today = fmt_money(active_film.get("worldwide_today"))
    threshold_fmt = fmt_money(threshold) if threshold else "—"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{headline}*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Film:*\n{active_title}"},
                {"type": "mrkdwn", "text": f"*Comparison:*\n{comp_title}"},
                {"type": "mrkdwn", "text": f"*Axis:*\n{axis.capitalize()}"},
                {"type": "mrkdwn", "text": f"*Kind:*\n{kind.replace('_', ' ').title()}"},
                {"type": "mrkdwn", "text": f"*Category:*\n{category.replace('_', ' ').title()}"},
                {"type": "mrkdwn", "text": f"*Milestone:*\n{threshold_fmt}"},
                {"type": "mrkdwn", "text": f"*Dom Today:*\n{dom_today}"},
                {"type": "mrkdwn", "text": f"*WW Today:*\n{ww_today}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{angle}_"},
        },
        {"type": "divider"},
    ]
    _post(webhook_url, blocks)


def post_failure(webhook_url: str, error: Exception, workflow_url: str = "") -> None:
    """Post an error/failure alert."""
    text = f":x: *Faramir failed*\n```{type(error).__name__}: {error}```"
    if workflow_url:
        text += f"\n<{workflow_url}|View workflow run>"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    ]
    _post(webhook_url, blocks)


def post_init(webhook_url: str, film_count: int) -> None:
    """Post initialization / cold-start notice."""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":rocket: *Faramir cold-start* — initialized with {film_count} active film"
                    f"{'s' if film_count != 1 else ''} in state. No picks on first run."
                ),
            },
        }
    ]
    _post(webhook_url, blocks)
