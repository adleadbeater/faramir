"""Fake Slack workspace served through FakeHTTP.

Covers the endpoints Faramir uses today (incoming webhook, chat.postMessage,
reactions.get, conversations.replies) plus conversations.history, which the
delivery reconciliation in Increment 6 will need. Messages keep any
``metadata`` passed to chat.postMessage.
"""

from __future__ import annotations

import requests

from tests.fakes.fake_http import FakeHTTP, FakeResponse

WEBHOOK_PREFIX = "https://hooks.slack.com/"
API = "https://slack.com/api/"


class FakeSlack:
    def __init__(self, bot_id: str = "B0FARAMIR"):
        self.bot_id = bot_id
        self.webhook_posts: list[dict] = []
        self.messages: list[dict] = []  # chat.postMessage results, in order
        self.reactions: dict[str, list[dict]] = {}
        self.replies: dict[str, list[dict]] = {}
        self._next_ts = 1_700_000_000
        self._post_failures: list = []

    # ── failure injection ────────────────────────────────────────────────
    def fail_next_post(self, error: str | None = "ratelimited", timeout: bool = False, deliver: bool = False):
        """Queue a failure for the next chat.postMessage.

        error   -> Slack returns ok:false with this error (nothing delivered)
        timeout -> raise requests.Timeout; if deliver=True the message is still stored
        """
        self._post_failures.append({"error": error, "timeout": timeout, "deliver": deliver})

    # ── handlers ─────────────────────────────────────────────────────────
    def _webhook(self, url, json=None, **_):
        self.webhook_posts.append({"url": url, "payload": json})
        return FakeResponse(200, text="ok")

    def _store(self, payload: dict) -> dict:
        self._next_ts += 1
        msg = {
            "ts": f"{self._next_ts}.000100",
            "channel": payload.get("channel"),
            "blocks": payload.get("blocks"),
            "text": payload.get("text", ""),
            "metadata": payload.get("metadata"),
            "bot_id": self.bot_id,
        }
        self.messages.append(msg)
        return msg

    def _post_message(self, url, json=None, headers=None, **_):
        if self._post_failures:
            f = self._post_failures.pop(0)
            if f["timeout"]:
                if f["deliver"]:
                    self._store(json or {})
                raise requests.Timeout("fake slack timeout")
            return FakeResponse(200, {"ok": False, "error": f["error"]})
        msg = self._store(json or {})
        return FakeResponse(200, {"ok": True, "ts": msg["ts"], "channel": msg["channel"]})

    def _reactions_get(self, url, params=None, **_):
        ts = (params or {}).get("timestamp")
        return FakeResponse(200, {"ok": True, "message": {"ts": ts, "reactions": self.reactions.get(ts, [])}})

    def _replies(self, url, params=None, **_):
        ts = (params or {}).get("ts")
        parent = {"ts": ts, "text": "parent", "bot_id": self.bot_id}
        return FakeResponse(200, {"ok": True, "messages": [parent] + self.replies.get(ts, [])})

    def _history(self, url, params=None, **_):
        params = params or {}
        oldest = float(params.get("oldest", 0))
        include_md = str(params.get("include_all_metadata", "")).lower() in ("1", "true")
        msgs = []
        for m in reversed(self.messages):
            if float(m["ts"]) < oldest:
                continue
            out = {k: v for k, v in m.items() if k != "metadata"}
            if include_md and m.get("metadata"):
                out["metadata"] = m["metadata"]
            msgs.append(out)
        return FakeResponse(200, {"ok": True, "messages": msgs, "has_more": False})

    def attach(self, http: FakeHTTP) -> "FakeSlack":
        http.route("POST", WEBHOOK_PREFIX, self._webhook)
        http.route("POST", API + "chat.postMessage", self._post_message)
        http.route("GET", API + "reactions.get", self._reactions_get)
        http.route("GET", API + "conversations.replies", self._replies)
        http.route("GET", API + "conversations.history", self._history)
        return self
