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


# ── Phase 3: assignment and published-article matching ──────────────────

# A Slack handle is one token; a written name is one or two capitalized words ("to Dana Lee for").
ASSIGNEE_RE = re.compile(r"(?i:\bassign(?:ed|ing)?\s+(?:it\s+|this\s+)?to)\s+(?:@([\w.'-]+)|([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)?))")
MATCH_WINDOW_DAYS = 7
MATCH_THRESHOLD = 0.5
AMBIGUOUS_MARGIN = 0.05
_WORD = re.compile(r"[a-z0-9$]+")


def assignee(text: str) -> str | None:
    m = ASSIGNEE_RE.search(text or "")
    return (m.group(1) or m.group(2)).strip() if m else None


def _norm(s: str) -> str:
    return " ".join(_WORD.findall((s or "").lower()))


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower())) - {"the", "a", "an", "of", "at", "in", "to", "and", "box", "office"}


def match_published(suggestions: list[dict], articles: list, window_days: int = MATCH_WINDOW_DAYS) -> list[dict]:
    """Link delivered suggestions to the MovieWeb article that covered them.

    An explicit MovieWeb URL in the suggestion's thread wins. Otherwise: a News-team article
    (Regular, Publish) published within ``window_days`` of the suggestion, whose title names the
    active film, scored on headline overlap; a near-tie between two articles is reported as
    ambiguous and not linked (never guessed).
    """
    from datetime import timedelta

    out = []
    pool = [a for a in articles if a.page_type.strip().lower() == "regular" and a.pub_type.strip().lower() == "publish"
            and a.team() == "news"]
    for s in suggestions:
        if str(s.get("delivery_state") or "") != "posted":
            continue
        url = URL_RE.search(str(s.get("thread_feedback") or ""))
        if url:
            out.append({"suggestion_id": s["suggestion_id"], "url": url.group(0).rstrip(".,)"), "method": "thread_url",
                        "confidence": 1.0, "ambiguous": False})
            continue
        try:
            start = date.fromisoformat(str(s.get("run_date"))[:10])
        except ValueError:
            continue
        film = _norm(s.get("active_title", ""))
        scored = []
        for a in pool:
            d = a.pub_date.date()
            if not (start <= d <= start + timedelta(days=window_days)) or not film or film not in _norm(a.title):
                continue
            ht, at = _tokens(s.get("headline", "")), _tokens(a.title)
            overlap = len(ht & at) / len(ht | at) if ht | at else 0
            comp_bonus = 0.2 if s.get("comp_title") and _norm(s["comp_title"]) in _norm(a.title) else 0
            scored.append((round(0.5 + 0.3 * overlap + comp_bonus, 3), a))
        scored.sort(key=lambda t: -t[0])
        if not scored or scored[0][0] < MATCH_THRESHOLD:
            continue
        ambiguous = len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUOUS_MARGIN
        out.append({"suggestion_id": s["suggestion_id"], "url": None if ambiguous else scored[0][1].url,
                    "candidates": [a.url for _, a in scored[:3]] if ambiguous else None,
                    "method": "cerebro_title_match", "confidence": scored[0][0], "ambiguous": ambiguous})
    return out
