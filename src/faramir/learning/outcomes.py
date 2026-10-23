"""Age-comparable outcomes (plan §5).

Cerebro sessions are cumulative per article, so an outcome is only comparable at a fixed article
age. Faramir records each linked article's cumulative figures at every snapshot it sees
(``outcome_observations`` ledger stream, with the article's age at that snapshot) and uses the
observation closest to day 14 **inside** a 14–21-day window [CAL]. It never extrapolates: an
article observed only at day 3 or day 90 has no 14-day outcome (``no_age_comparable_observation``).
"""

from __future__ import annotations

from dataclasses import dataclass

from faramir.analytics.cerebro_snapshot import (DISCOVER_VISIBLE_IMPRESSIONS, FAILURE_SESSIONS, HEAVY_HITTER_SESSIONS,
                                                Snapshot)


def observe(snap: Snapshot, urls: set[str]) -> list[dict]:
    """Ledger rows: one per linked article per snapshot date."""
    out = []
    for a in snap.articles:
        if a.url in urls:
            out.append({"id": f"{a.url}|{snap.as_of.isoformat()}", "url": a.url, "snapshot_as_of": snap.as_of.isoformat(),
                        "age_days": a.age_days(snap.as_of), "cumulative_sessions": a.sessions,
                        "discover_impressions": a.discover_impressions, "discover_clicks": a.discover_clicks,
                        "search_clicks": a.search_clicks, "content_type": a.content_type, "team": a.team()})
    return out


@dataclass
class Outcome:
    url: str
    status: str                       # ok | no_age_comparable_observation | no_observation
    sessions: float | None = None
    age_days: int | None = None
    failure: bool | None = None
    heavy_hitter: bool | None = None
    discover_visible: bool | None = None
    discover_clicks: float | None = None


def at_age(observations: list[dict], url: str, target: int = 14, window: tuple[int, int] = (14, 21)) -> Outcome:
    obs = [o for o in observations if o["url"] == url]
    if not obs:
        return Outcome(url, "no_observation")
    inside = [o for o in obs if window[0] <= o["age_days"] <= window[1]]
    if not inside:
        return Outcome(url, "no_age_comparable_observation")
    o = min(inside, key=lambda o: (abs(o["age_days"] - target), o["snapshot_as_of"]))
    s = o["cumulative_sessions"]
    di = o.get("discover_impressions")
    return Outcome(url, "ok", s, o["age_days"], s < FAILURE_SESSIONS, s > HEAVY_HITTER_SESSIONS,
                   (di > DISCOVER_VISIBLE_IMPRESSIONS) if di is not None else None, o.get("discover_clicks"))
