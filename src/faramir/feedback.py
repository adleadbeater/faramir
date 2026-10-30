from __future__ import annotations
"""Read Slack reactions and thread replies on yesterday's picks and write back to suggestions sheet.

Requires SLACK_BOT_TOKEN with scopes: reactions:read, channels:history (or groups:history
for private channels). SLACK_CHANNEL_ID is the channel where picks are posted.

Runs at the top of the daily pipeline before new picks are posted.
"""

import logging
from datetime import date, timedelta

import requests

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
TIMEOUT = 10


def _get(bot_token: str, endpoint: str, params: dict) -> dict:
    """GET a Slack Web API endpoint. Returns parsed JSON."""
    resp = requests.get(
        f"{SLACK_API}/{endpoint}",
        headers={"Authorization": f"Bearer {bot_token}"},
        params=params,
        timeout=TIMEOUT,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {endpoint} error: {data.get('error')}")
    return data


def read_reactions(bot_token: str, channel_id: str, ts: str) -> dict:
    """Fetch reaction counts and thread reply count for a single message ts.

    Returns only the fields that were actually fetched: thumbs_up/thumbs_down
    when reactions.get succeeded, thread_replies/thread_feedback when
    conversations.replies succeeded. A failed Slack read therefore never
    overwrites stored feedback with zeros.
    """
    result: dict = {}

    # Reactions on the message
    try:
        data = _get(bot_token, "reactions.get", {"channel": channel_id, "timestamp": ts, "full": True})
        message = data.get("message", {})
        result["thumbs_up"] = 0
        result["thumbs_down"] = 0
        for reaction in message.get("reactions", []):
            name = reaction.get("name", "")
            count = reaction.get("count", 0)
            if name in ("+1", "thumbsup", "white_check_mark"):
                result["thumbs_up"] += count
            elif name in ("-1", "thumbsdown", "x"):
                result["thumbs_down"] += count
    except Exception as exc:
        logger.warning("read_reactions: could not fetch reactions for ts=%s: %s", ts, exc)

    # Thread replies
    try:
        data = _get(bot_token, "conversations.replies", {
            "channel": channel_id,
            "ts": ts,
            "limit": 20,
        })
        messages = data.get("messages", [])
        # First message is the parent — replies are the rest
        replies = messages[1:]
        result["thread_replies"] = len(replies)
        result["thread_feedback"] = ""
        if replies:
            # Concatenate non-bot reply text as feedback signal
            texts = [r.get("text", "").strip() for r in replies if not r.get("bot_id")]
            result["thread_feedback"] = " | ".join(t for t in texts if t)[:500]
    except Exception as exc:
        logger.warning("read_reactions: could not fetch thread for ts=%s: %s", ts, exc)

    return result


def collect_feedback(
    bot_token: str,
    channel_id: str,
    suggestions: list[dict],
    target_date: date,
) -> list[dict]:
    """For all suggestions from target_date that have a slack_ts, fetch reactions.

    Returns the same list with thumbs_up, thumbs_down, thread_replies, thread_feedback populated.
    """
    date_str = str(target_date)
    updated = []
    for row in suggestions:
        if str(row.get("run_date", "")) != date_str:
            updated.append(row)
            continue
        ts = row.get("slack_ts", "")
        if not ts:
            updated.append(row)
            continue
        feedback = read_reactions(bot_token, channel_id, ts)
        row = {**row, **feedback}
        logger.info(
            "feedback for '%s': 👍%s 👎%s replies=%s",
            row.get("active_title", ""),
            feedback.get("thumbs_up", "n/a"),
            feedback.get("thumbs_down", "n/a"),
            feedback.get("thread_replies", "n/a"),
        )
        updated.append(row)
    return updated


def fetch_feedback(bot_token: str, channel_id: str, rows: list[dict], target_date: date) -> dict[str, dict]:
    """Feedback for target_date's posted suggestions, keyed by Slack ts.

    Each value contains only successfully fetched fields (see read_reactions).
    """
    date_str = str(target_date)
    out: dict[str, dict] = {}
    for row in rows:
        ts = str(row.get("slack_ts", "") or "").strip()
        if str(row.get("run_date", "")) != date_str or not ts or ts in out:
            continue
        fields = read_reactions(bot_token, channel_id, ts)
        if fields:
            out[ts] = fields
    return out
