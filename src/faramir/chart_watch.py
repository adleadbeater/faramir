"""When is The Numbers' daily chart ready? Learned film by film from what the daily job sees.

Studios report the day before's grosses at different times and The Numbers adds each film as it reports
(2026-10-30: 1 film at 10am, 10 at 11:30, 13 at noon). Some distributors don't report daily at all and are
back-filled days later, so the chart is never complete on the day: waiting for a share of the eventual
chart waits all day for films that won't come (measured on 150 archived charts, 2025–26: after 3pm a
median chart lists ~55% of its eventual films, and a top-10 film is missing a quarter of the time).

So the job watches instead. Every check records the time each film first appears on the chart, in
out/local_store/chart_watch.json. A film reports at about the same time each day, so the chart is ready when
every notable film that reported same-day on the last watched day is in again, or once the latest of those
learned times (plus GRACE) has passed, whichever comes first. Films that never report same-day are never
waited for. With no history yet (cold start) it falls back to the old rule: 75% of the day before, or
COLD_CUTOFF. It never waits past HARD_CUTOFF.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
TOP = 10                 # only the top films by gross are waited for; the tail never holds the chart back
MIN_GROSS = 25_000       # …and only films that made at least this the last watched day
LOOKBACK = 3             # a film's expected time = its latest first-seen time over its last 3 watched days
GRACE = 20               # minutes past a film's expected time before giving up on it for the day
COLD_SHARE = 0.75        # cold start: the old rule
MIN_ROWS = 10
COLD_CUTOFF = 14 * 60 + 30
HARD_CUTOFF = 15 * 60    # never wait past 3pm


def now_minutes(now: datetime | None = None) -> int:
    t = (now or datetime.now(ET)).astimezone(ET)
    return t.hour * 60 + t.minute


def hhmm(m: int) -> str:
    h, mm = divmod(int(m), 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d}{'am' if h < 12 else 'pm'}"


def _min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"days": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(store: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record(store: dict, day: date, minute: int, films: list[tuple[str, int]]) -> None:
    """One check of ``day``'s chart at ``minute`` (ET minutes after midnight): films as (title, gross)."""
    d = store.setdefault("days", {}).setdefault(day.isoformat(), {"checks": [], "first_seen": {}, "gross": {}})
    d["checks"].append([f"{minute // 60:02d}:{minute % 60:02d}", len(films)])
    for title, gross in films:
        d["first_seen"].setdefault(title, f"{minute // 60:02d}:{minute % 60:02d}")
        d["gross"][title] = gross


def expectations(store: dict, day: date) -> dict[str, int]:
    """Films expected on ``day``'s chart, and by when: the notable films that reported same-day on the
    last watched day before it, each at the latest time it first appeared over its last LOOKBACK days."""
    days = sorted(k for k in store.get("days", {}) if k < day.isoformat())
    if not days:
        return {}
    last = store["days"][days[-1]]
    top = [t for t in sorted(last["first_seen"], key=lambda t: -(last["gross"].get(t) or 0))[:TOP]
           if (last["gross"].get(t) or 0) >= MIN_GROSS]
    out = {}
    for title in top:
        seen = [_min(store["days"][k]["first_seen"][title]) for k in days if title in store["days"][k]["first_seen"]]
        out[title] = max(seen[-LOOKBACK:])
    return out


def decide(store: dict, day: date, minute: int, present: list[str], day_before_rows: int = 0) -> tuple[bool, str]:
    """Is ``day``'s chart ready at ``minute``, with ``present`` films on it? Returns (ready, why); a
    ready-but-incomplete chart's why starts with "partial"."""
    if not present:
        return False, "not posted yet"
    expected = expectations(store, day)
    if not expected:
        need = max(MIN_ROWS, round(COLD_SHARE * day_before_rows)) if day_before_rows else MIN_ROWS
        if len(present) >= need:
            return True, f"{len(present)} films"
        why = f"{len(present)} films so far (the day before had {day_before_rows}); no reporting history yet"
        return (True, f"partial: {why}") if minute >= COLD_CUTOFF else (False, f"partial: {why}")
    have = set(present)
    missing = sorted((t for t in expected if t not in have), key=lambda t: expected[t])
    if not missing:
        return True, f"{len(present)} films; all {len(expected)} that usually report same-day are in"
    deadline = min(HARD_CUTOFF, max(expected[t] for t in missing) + GRACE)
    names = ", ".join(f"{t} (usually by {hhmm(expected[t])})" for t in missing[:4]) + (" …" if len(missing) > 4 else "")
    if minute >= deadline:
        return True, f"partial: {len(present)} films; still missing {names}"
    return False, f"partial: {len(present)} films; waiting for {names} until {hhmm(deadline)}"


def films_on_chart(day: date, get=None) -> list[tuple[str, int]]:
    """The films The Numbers' daily chart for ``day`` lists so far, as (title, gross); [] if not posted."""
    from faramir.tn_chart import daily_url, parse_chart
    from faramir.weekend_collect import USER_AGENT
    if get is None:
        import requests
        get = requests.get
    try:
        resp = get(daily_url(day), headers={"User-Agent": USER_AGENT}, timeout=30)
        if not resp.ok:
            return []
        return [(r.title_reported, r.gross or 0) for r in parse_chart(resp.text, daily_url(day), "daily", day).rows]
    except Exception:  # noqa: BLE001 — treat as not posted; the job retries
        return []


def check(day: date, path: str, now: datetime | None = None, fetch=films_on_chart) -> tuple[bool, str]:
    """Is ``day``'s chart ready? Records the check when ``day`` is yesterday (a live check), so the job
    learns every day; a rerun of a past day learns nothing and treats the chart as long posted."""
    films = fetch(day)
    now = now or datetime.now(ET)
    minute = now_minutes(now)
    store = load(path)
    live = day == now.astimezone(ET).date() - timedelta(days=1)
    if live and films:
        record(store, day, minute, films)
        save(store, path)
    if live:
        before = store["days"].get((day - timedelta(days=1)).isoformat(), {}).get("first_seen")
        before_rows = len(before) if before else len(fetch(day - timedelta(days=1)))
    else:
        minute, before_rows = HARD_CUTOFF, len(fetch(day - timedelta(days=1)))
    return decide(store, day, minute, [t for t, _ in films], before_rows)


def delivered(path: str, run_date: date) -> str | None:
    """The run id that already delivered ``run_date``'s cards, if any."""
    return load(path).get("delivered", {}).get(run_date.isoformat())


def mark_delivered(path: str, run_date: date, run_id: str) -> None:
    store = load(path)
    store.setdefault("delivered", {})[run_date.isoformat()] = run_id
    save(store, path)
