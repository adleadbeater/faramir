"""Sanitized Cerebro snapshot: MovieWeb article performance, under the established contract.

Contract (shared with aragorn/arwen ``src/services/cerebro.js``):

* **Allow-list only.** Recognised columns (UI-export and BigQuery header aliases map to the
  canonical names below); any column that looks sensitive (writer, editor, author, cost, pay,
  rate, fee, salary …) makes the file refuse to load; other unknown columns are ignored.
* **Filters.** Regular pages only, ``Publish`` only (``Republish`` excluded), brand ``MW``.
* **Team pools are never mixed.** News = News, Flash News, Niche News. Features/Lists =
  Feature, Mini Feature, List. Video, Review and everything else belong to neither.
* **Sessions are cumulative per article** up to the snapshot date. They are reported as
  "cumulative sessions per article" with the article ages they cover — never as traffic for a
  calendar period, and never compared across very different ages without saying so.
* **Maturity.** Articles younger than ``min_age_days`` (default 14 [CAL]) are excluded from
  demand figures; they haven't accumulated their traffic.

Faramir never commits a snapshot. Locally the path comes from ``FARAMIR_CEREBRO_SNAPSHOT`` (the
sanitized file scripts/sync-cerebro.sh writes); in production ``from_sheet`` reads the Cerebro sheet with
the service account, fetching only allow-listed columns, so sensitive columns are never downloaded.
"""

from __future__ import annotations

import csv
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime

CANONICAL = ["title", "url", "pub_date", "pub_type", "brand", "segment", "net_cat", "pri_cat", "pri_tag", "tags",
             "content_type", "page_type", "intent", "sessions", "target_sessions", "pageviews", "discover_clicks",
             "discover_impressions", "search_clicks"]
ALIASES = {
    # MovieWeb UI export
    "Title": "title", "URL": "url", "Pub. Date": "pub_date", "Pub. Type": "pub_type", "Brand": "brand",
    "Segment": "segment", "Net. Cat": "net_cat", "Network Category": "net_cat", "Pri. Cat": "pri_cat",
    "Primary Category": "pri_cat", "Pri. Tag": "pri_tag", "Primary Tag": "pri_tag", "Tags": "tags",
    "Content Type": "content_type", "Page Type": "page_type", "Intent": "intent", "Actual Sessions": "sessions",
    "Sessions": "sessions", "Target Sessions": "target_sessions", "Pageviews": "pageviews",
    "Discover Clicks": "discover_clicks", "Discover Impressions": "discover_impressions", "Search Clicks": "search_clicks",
    # BigQuery export
    "ArticleTitle": "title", "PubDate": "pub_date", "PubType": "pub_type", "BrandName": "brand",
    "segmentName": "segment", "ContentType": "content_type", "PageType": "page_type", "PrimaryTag": "pri_tag",
    "PrimaryCategory": "pri_cat",
    # Alex's daily BigQuery export (Cerebro Data/cerebro_MW.csv) and the production Cerebro sheet
    "PubDatetime": "pub_date", "PriTag": "pri_tag", "AllTags": "tags", "NetCat": "net_cat", "PriCat": "pri_cat",
    "ActSess": "sessions", "ActTargetSess": "target_sessions", "ActPageviews": "pageviews",
    "ActDiscoverClicks": "discover_clicks", "ActDiscoverImpressions": "discover_impressions",
    "ActSeachClicks": "search_clicks", "pub_datetime": "pub_date", "act_sess": "sessions",
}
SENSITIVE = re.compile(r"writer|editor|author|byline|cost|pay(ment)?|rate|fee|salary|budget|invoice|wage|earning|email",
                       re.I)
TEAM_POOLS = {"news": {"news", "flash news", "niche news"}, "features_lists": {"feature", "mini feature", "list"}}
FAILURE_SESSIONS = 500          # plan §5
HEAVY_HITTER_SESSIONS = 25_000  # plan §5
DISCOVER_VISIBLE_IMPRESSIONS = 1_000
DEFAULT_MIN_AGE_DAYS = 14


class SensitiveColumns(ValueError):
    pass


@dataclass
class Article:
    title: str
    url: str
    pub_date: datetime
    pub_type: str
    brand: str
    segment: str
    pri_cat: str
    pri_tag: str
    tags: list[str]
    content_type: str
    page_type: str
    sessions: float
    discover_impressions: float | None
    discover_clicks: float | None
    search_clicks: float | None

    def age_days(self, as_of: date) -> int:
        return (as_of - self.pub_date.date()).days

    def team(self) -> str | None:
        ct = self.content_type.strip().lower()
        return next((t for t, types in TEAM_POOLS.items() if ct in types), None)


def _num(v) -> float | None:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s else None
    except ValueError:
        return None


def _date(v) -> datetime | None:
    s = str(v or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


@dataclass
class Snapshot:
    articles: list[Article]
    as_of: date
    as_of_basis: str            # "explicit" | "latest_pub_date"
    path: str
    dropped_rows: int = 0
    ignored_columns: list[str] = field(default_factory=list)

    def pool(self, team: str, min_age_days: int = DEFAULT_MIN_AGE_DAYS, brand: str = "MW") -> list[Article]:
        """The contract pool: Regular, Publish, brand, one team, matured."""
        if team not in TEAM_POOLS:
            raise ValueError(f"unknown team {team!r}; pools are {sorted(TEAM_POOLS)} and are never mixed")
        return [a for a in self.articles
                if a.page_type.strip().lower() == "regular" and a.pub_type.strip().lower() == "publish"
                and a.brand.strip().upper() == brand and a.team() == team and a.age_days(self.as_of) >= min_age_days]


def _mapping(header: list[str]) -> tuple[dict[int, str], list[str], list[str]]:
    mapping, ignored, sensitive = {}, [], []
    for i, h in enumerate(header):
        name = ALIASES.get(h.strip(), h.strip() if h.strip() in CANONICAL else None)
        if name and name not in mapping.values():
            mapping[i] = name
        elif not name and SENSITIVE.search(h):
            sensitive.append(h)
        elif not name:
            ignored.append(h)
    return mapping, ignored, sensitive


def _build(mapping: dict[int, str], rows, source: str, as_of: date | None, ignored: list[str]) -> Snapshot:
    missing = {"title", "pub_date", "pub_type", "content_type", "page_type", "sessions"} - set(mapping.values())
    if missing:
        raise ValueError(f"{source}: snapshot lacks required columns {sorted(missing)}")
    articles, dropped = [], 0
    for raw in rows:
        r = {mapping[i]: v for i, v in enumerate(raw) if i in mapping}
        pub, sessions = _date(r.get("pub_date")), _num(r.get("sessions"))
        if pub is None or sessions is None:
            dropped += 1
            continue
        articles.append(Article(
            title=r.get("title", ""), url=r.get("url", ""), pub_date=pub, pub_type=r.get("pub_type", ""),
            brand=r.get("brand", "MW") or "MW", segment=r.get("segment", ""), pri_cat=r.get("pri_cat", ""),
            pri_tag=r.get("pri_tag", ""), tags=[t.strip() for t in re.split(r"[|;,]", r.get("tags", "")) if t.strip()],
            content_type=r.get("content_type", ""), page_type=r.get("page_type", ""), sessions=sessions,
            discover_impressions=_num(r.get("discover_impressions")), discover_clicks=_num(r.get("discover_clicks")),
            search_clicks=_num(r.get("search_clicks"))))
    basis = "explicit" if as_of else "latest_pub_date"
    as_of = as_of or max((a.pub_date.date() for a in articles), default=date.today())
    return Snapshot(articles, as_of, basis, source, dropped, ignored)


def load(path: str, as_of: date | None = None) -> Snapshot:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        mapping, ignored, sensitive = _mapping(next(reader))
        if sensitive:
            raise SensitiveColumns(f"{path}: refusing a snapshot with sensitive columns {sensitive} — "
                                   "use the sanitized export (allow-list only)")
        return _build(mapping, reader, path, as_of, ignored)


SHEET_TAB = "MovieWeb Article Analysis"


def from_sheet(gc, sheet_id: str, tab: str = SHEET_TAB, as_of: date | None = None) -> Snapshot:
    """The production Cerebro sheet, read column by column: only allow-listed columns are fetched, so
    writer, editor and pay columns never leave the sheet (they are skipped, not refused)."""
    ws = gc.open_by_key(sheet_id).worksheet(tab)
    mapping, ignored, _ = _mapping(ws.row_values(1))
    cols = {i: ws.col_values(i + 1)[1:] for i in mapping}
    n = max((len(c) for c in cols.values()), default=0)
    width = max(mapping, default=-1) + 1
    rows = ([cols[i][k] if i in cols and k < len(cols[i]) else "" for i in range(width)] for k in range(n))
    return _build(mapping, rows, f"sheet:{tab}", as_of, ignored)


def configured(as_of: date | None = None) -> Snapshot | None:
    path = os.environ.get("FARAMIR_CEREBRO_SNAPSHOT")
    return load(path, as_of) if path and os.path.exists(path) else None


@dataclass
class Metrics:
    """Cumulative sessions per article (to the snapshot date) for one set of articles."""
    n: int
    mean: float | None
    median: float | None
    failure_rate: float | None
    heavy_hitter_rate: float | None
    discover_visible_rate: float | None
    age_days_min: int | None
    age_days_median: float | None
    age_days_max: int | None
    basis: str = "cumulative sessions per article to the snapshot date (not calendar-period traffic)"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def metrics(articles: list[Article], as_of: date) -> Metrics:
    if not articles:
        return Metrics(0, None, None, None, None, None, None, None, None)
    s = [a.sessions for a in articles]
    ages = [a.age_days(as_of) for a in articles]
    di = [a for a in articles if a.discover_impressions is not None]
    return Metrics(
        n=len(s), mean=round(statistics.fmean(s), 1), median=statistics.median(s),
        failure_rate=round(sum(1 for x in s if x < FAILURE_SESSIONS) / len(s), 4),
        heavy_hitter_rate=round(sum(1 for x in s if x > HEAVY_HITTER_SESSIONS) / len(s), 4),
        discover_visible_rate=round(sum(1 for a in di if a.discover_impressions > DISCOVER_VISIBLE_IMPRESSIONS) / len(di), 4) if di else None,
        age_days_min=min(ages), age_days_median=statistics.median(ages), age_days_max=max(ages))


def matches(article: Article, keywords: list[str]) -> bool:
    hay = " ".join([article.pri_tag, " ".join(article.tags), article.title]).lower()
    return any(k.lower() in hay for k in keywords if k)
