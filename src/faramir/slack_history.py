"""Read Faramir's own delivery markers back from Slack channel history.

A pick message is recognised by EITHER
  * metadata ``{"event_type": "faramir_pick", "event_payload": {"suggestion_id": ...}}``
    (returned only when the token may read metadata), OR
  * the visible ``ref <suggestion_id>`` in its context line (readable with the
    ``channels:history`` / ``groups:history`` scope Faramir already uses),
and the daily header by ``faramir_header`` metadata or ``ref daily-header-YYYY-MM-DD``.

The result is complete or explicitly not ok. Any request failure, missing
scope, rate limit or a page cap with more pages remaining gives ``ok=False`` —
callers must never read a failed history fetch as "not posted".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

API = "https://slack.com/api/conversations.history"
PAGE_LIMIT = 200
MAX_PAGES = 25
TIMEOUT = 10
REF_RE = re.compile(r"\bref ((?:daily-header-\d{4}-\d{2}-\d{2})|[0-9a-f]{16}(?:~\d+)?|legacy-[0-9a-f]{12}(?:~\d+)?|adhoc-[0-9a-f]{12})\b")


@dataclass
class HistoryResult:
    ok: bool
    error: str | None = None
    picks: dict[str, list[str]] = field(default_factory=dict)    # suggestion_id -> [ts, ...]
    headers: dict[str, list[str]] = field(default_factory=dict)  # run_date -> [ts, ...]
    pages: int = 0
    messages: int = 0
    matched_by_metadata: int = 0
    matched_by_ref: int = 0


def _texts(msg: dict) -> list[str]:
    out = [msg.get("text") or ""]
    for block in msg.get("blocks") or []:
        for el in block.get("elements") or []:
            if isinstance(el, dict) and isinstance(el.get("text"), str):
                out.append(el["text"])
        text = block.get("text")
        if isinstance(text, dict) and isinstance(text.get("text"), str):
            out.append(text["text"])
    return out


def _classify(msg: dict, result: HistoryResult) -> None:
    if not (msg.get("bot_id") or msg.get("subtype") == "bot_message" or msg.get("app_id")):
        return  # only bot/app messages can be Faramir's
    ts = msg.get("ts")
    md = msg.get("metadata") or {}
    payload = md.get("event_payload") or {}
    if md.get("event_type") == "faramir_pick" and payload.get("suggestion_id"):
        result.picks.setdefault(str(payload["suggestion_id"]), []).append(ts)
        result.matched_by_metadata += 1
        return
    if md.get("event_type") == "faramir_header" and payload.get("run_date"):
        result.headers.setdefault(str(payload["run_date"]), []).append(ts)
        result.matched_by_metadata += 1
        return
    for text in _texts(msg):
        m = REF_RE.search(text)
        if m:
            ref = m.group(1)
            if ref.startswith("daily-header-"):
                result.headers.setdefault(ref[len("daily-header-"):], []).append(ts)
            else:
                result.picks.setdefault(ref, []).append(ts)
            result.matched_by_ref += 1
            return


def fetch_tagged(bot_token: str, channel_id: str, oldest: float, latest: float | None = None,
                 max_pages: int = MAX_PAGES) -> HistoryResult:
    """All Faramir-tagged messages in [oldest, latest], following every page."""
    result = HistoryResult(ok=False)
    cursor = None
    while True:
        if result.pages >= max_pages:
            result.error = f"history_truncated: more than {max_pages} pages"
            return result
        params = {"channel": channel_id, "oldest": f"{oldest:.6f}", "inclusive": "true",
                  "limit": PAGE_LIMIT, "include_all_metadata": "true"}
        if latest is not None:
            params["latest"] = f"{latest:.6f}"
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(API, headers={"Authorization": f"Bearer {bot_token}"}, params=params, timeout=TIMEOUT)
        except Exception as exc:
            result.error = f"request_failed: {type(exc).__name__}"
            return result
        if getattr(resp, "status_code", 200) != 200:
            result.error = f"http_{resp.status_code}"
            return result
        try:
            data = resp.json()
        except Exception:
            result.error = "unreadable_response"
            return result
        if not data.get("ok"):
            result.error = str(data.get("error") or "unknown_error")  # e.g. missing_scope, not_in_channel, ratelimited
            return result
        result.pages += 1
        for msg in data.get("messages") or []:
            result.messages += 1
            _classify(msg, result)
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            if data.get("has_more"):
                result.error = "has_more_without_cursor"
                return result
            result.ok = True
            return result
