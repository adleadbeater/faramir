"""Editorial status capture (Phase 1: light; extended in Phase 3).

Statuses: ``suggested`` → ``accepted`` / ``rejected`` / ``assigned`` → ``published``, or
``expired`` when nothing happened within the window. Derived only from feedback Faramir
already collects on each suggestion row (thumbs up/down, thread replies); reason codes for
rejections are read from the thread text (plan §5 feedback routing).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

STATUSES = ("suggested", "accepted", "assigned", "published", "rejected", "expired")
EXPIRE_DAYS = 14
REASON_PATTERNS = [
    ("already_covered", r"\b(already (covered|ran|did|wrote)|covered (it|this|already)|dupe|duplicate)\b"),
    ("timing", r"\b(timing|too late|too early|stale|old news|late)\b"),
    ("questionable_data", r"\b(wrong|incorrect|data|numbers? (look|are) off|not right|inaccurate)\b"),
    ("weak_comparison", r"\b(weak|meh|not (a )?(great|good|interesting)|boring|stretch)\b"),
    ("audience_appeal", r"\b(nobody cares|no interest|audience|niche|not our readers)\b"),
]
ASSIGNED_RE = re.compile(r"\b(assign(ed|ing)?|taking (it|this)|i'?ll (write|take)|on it)\b", re.I)
URL_RE = re.compile(r"https?://(?:www\.)?movieweb\.com/[^\s>|]+", re.I)


def _int(v) -> int:
    try:
        return int(str(v or "0").strip() or 0)
    except ValueError:
        return 0


def derive(row: dict, today: date) -> tuple[str, list[str], str | None]:
    """(status, reason_codes, published_url) for one suggestion row."""
    text = str(row.get("thread_feedback") or "")
    up, down = _int(row.get("thumbs_up")), _int(row.get("thumbs_down"))
    url = URL_RE.search(text)
    if url:
        return "published", ["url_in_thread"], url.group(0).rstrip(".,)")
    if down > up:
        codes = [code for code, pat in REASON_PATTERNS if re.search(pat, text, re.I)]
        return "rejected", codes or ["no_reason_given"], None
    if ASSIGNED_RE.search(text):
        return "assigned", ["assignment_in_thread"], None
    if up > 0:
        return "accepted", ["thumbs_up"], None
    try:
        age = (today - date.fromisoformat(str(row.get("run_date"))[:10])).days
    except ValueError:
        age = 0
    if age > EXPIRE_DAYS:
        return "expired", ["no_response"], None
    return "suggested", [], None


def capture(rows: list[dict], today: date) -> list[dict]:
    """Ledger records for the current status of every delivered suggestion (id changes only when status does)."""
    out = []
    for r in rows:
        if str(r.get("delivery_state") or "") not in ("posted",):
            continue
        sid = str(r.get("suggestion_id") or "")
        if not sid:
            continue
        status, codes, url = derive(r, today)
        rid = hashlib.sha1(f"{sid}|{status}|{','.join(codes)}|{url or ''}".encode()).hexdigest()[:20]
        ev = {}
        try:
            import json
            ev = json.loads(r.get("evidence_json") or "{}")
        except ValueError:
            pass
        out.append({"id": rid, "suggestion_id": sid, "status": status, "reason_codes": codes, "published_url": url,
                    "observed": today.isoformat(), "run_date": str(r.get("run_date")), "headline": r.get("headline"),
                    "event_id": ev.get("event_id"), "strategy": ev.get("strategy")})
    return out
