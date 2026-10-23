"""Historical box-office headline analysis on the Cerebro snapshot (plan §5 "Priors").

Selects MovieWeb **News-team** box-office articles (``box office`` in the primary tag, tags or
title), matured ≥ 14 days, tags each headline's construction(s), and reports cumulative sessions
per article by construction against the box-office baseline, with n. A heuristic strategy label
(from the construction) is attached for the prior; every row is labelled
``historical_not_faramir``. A deterministic 10% sample is produced for the editor audit.

Everything is derived from the sanitized snapshot; results describe MovieWeb's past headlines,
not anything Faramir originated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from faramir.analytics.cerebro_snapshot import Snapshot, matches, metrics
from faramir.headlines.taxonomy import tag

BOX_OFFICE_KEYWORDS = ["box office"]
MIN_N = 8                     # below this an estimate is labelled experimental (plan §5)
AUDIT_SHARE = 0.10
STRATEGY_HINT = {"passes_comparison": "overtake", "milestone_crossing": "overtake", "anticipation": "chase_or_countdown",
                 "chart_position": "takeover_or_hold", "opening_weekend": "takeover", "contrast_upset": "dark_horse",
                 "ongoing_run": "legs_or_streak", "record_claim": "star_record_or_record"}


@dataclass
class ConstructionStat:
    construction: str
    n: int
    mean: float | None
    median: float | None
    lift_vs_baseline: float | None     # median / baseline median − 1
    failure_rate: float | None
    heavy_hitter_rate: float | None
    experimental: bool
    age_days_median: float | None = None   # cumulative sessions grow with age: compare ages before trusting a lift

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Analysis:
    as_of: str
    team: str
    baseline: dict
    constructions: dict[str, ConstructionStat]
    articles: int
    audit_sample: list[dict]
    label: str = "historical_not_faramir"

    def stat(self, construction: str) -> ConstructionStat | None:
        return self.constructions.get(construction)


def box_office_articles(snap: Snapshot, team: str = "news", min_age_days: int = 14):
    return [a for a in snap.pool(team, min_age_days) if matches(a, BOX_OFFICE_KEYWORDS)]


def analyze(snap: Snapshot, team: str = "news", min_age_days: int = 14) -> Analysis:
    arts = box_office_articles(snap, team, min_age_days)
    base = metrics(arts, snap.as_of)
    by: dict[str, list] = {}
    rows = []
    for a in arts:
        tags = tag(a.title)
        for t in tags:
            by.setdefault(t, []).append(a)
        rows.append((a, tags))
    stats = {}
    for name, group in by.items():
        m = metrics(group, snap.as_of)
        lift = (m.median / base.median - 1) if base.median and m.median is not None else None
        stats[name] = ConstructionStat(name, m.n, m.mean, m.median, round(lift, 3) if lift is not None else None,
                                       m.failure_rate, m.heavy_hitter_rate, m.n < MIN_N, m.age_days_median)
    audit = []
    for a, tags in rows:
        h = int(hashlib.sha1((a.url or a.title).encode()).hexdigest(), 16)
        if (h % 1000) < AUDIT_SHARE * 1000:
            audit.append({"url": a.url, "title": a.title, "pub_date": a.pub_date.date().isoformat(),
                          "constructions": tags, "strategy_hint": STRATEGY_HINT.get(tags[0], "none"),
                          "label": "historical_not_faramir", "editor_construction_ok": "", "editor_strategy_ok": ""})
    return Analysis(snap.as_of.isoformat(), team, base.as_dict(), stats, len(arts), audit)
