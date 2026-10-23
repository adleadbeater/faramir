"""Read-only check that the production Slack bot token supports Increment 6.

    SLACK_BOT_TOKEN=… SLACK_CHANNEL_ID=… python scripts/verify_slack_access.py

Makes only read calls (auth.test, conversations.history limit=20) and posts
nothing. Running it with the production token is itself an approval-gated step.

Required (delivery works):     chat:write, channels:history (or groups:history for a private channel)
Required for feedback:         reactions:read
Optional (metadata matching):  metadata.message:read — without it, reconciliation
                               falls back to the visible "ref <id>" in each message.
"""

from __future__ import annotations

import json
import os
import sys

import requests

REQUIRED_ANY = [("chat:write",), ("channels:history", "groups:history"), ("reactions:read",)]
OPTIONAL = ["metadata.message:read"]


def main() -> int:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    if not token or not channel:
        print("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {token}"}
    auth = requests.get("https://slack.com/api/auth.test", headers=headers, timeout=10)
    scopes = sorted(s.strip() for s in auth.headers.get("x-oauth-scopes", "").split(",") if s.strip())
    body = auth.json()
    hist = requests.get("https://slack.com/api/conversations.history", headers=headers, timeout=10,
                        params={"channel": channel, "limit": 20, "include_all_metadata": "true"}).json()
    missing = [" or ".join(group) for group in REQUIRED_ANY if not any(s in scopes for s in group)]
    report = {
        "auth_ok": body.get("ok"), "bot_id": body.get("bot_id"), "team": body.get("team"),
        "scopes": scopes, "missing_required": missing,
        "optional_present": {s: s in scopes for s in OPTIONAL},
        "history_ok": hist.get("ok"), "history_error": hist.get("error"),
        "history_messages_with_metadata": sum(1 for m in hist.get("messages", []) if m.get("metadata")),
    }
    print(json.dumps(report, indent=2))
    return 0 if body.get("ok") and not missing and hist.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
