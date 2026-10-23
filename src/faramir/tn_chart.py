"""The Numbers daily and weekend chart parser (P0-3, Increment 10). Parse only.

No fetching lives here: this module turns chart HTML that something else
obtained into a classified, validated ``Chart``. Live weekend collection is
Increment 11 and stays off until the source-permission question is resolved.

Layout (verified 2026-10-16 weekend / 2026-10-21 daily captures):

* ``<h1>``: "Weekend Domestic Box Office October 16, 2026" or
  "Daily Domestic Box Office Wednesday, October 21, 2026" — chart type and
  reporting date.
* ``table.chart-desktop`` (the ``chart-mobile`` twin is ignored). Header cells
  ``Rank, Prev, Title, Gross, [Daily Change], Weekly Change, Theaters, Theater
  Average, Total Gross, Days in Release``; "Daily Change" exists only on the
  daily chart. Columns are mapped by exact (whitespace-normalized) header
  name, so reordering is fine and an unknown or missing header is an error.
* Rows: only the top rows carry a rank number (1–15 weekend, 1–8 daily);
  lower rows show ``-``. Prev is ``(n)``, ``(new)`` or ``(-)``. A change cell is
  blank for a new film. Title links to ``/movie/<Slug>-(<year>)``.
* A one-cell spacer row, then the footer "Reporting movies: N" with the
  chart's total gross.

Rules:

* A missing or malformed value is ``None`` and an issue — never zero, never
  invented.
* ``source_rank`` is only what TN printed; ``derived_rank`` is Faramir's,
  computed from gross order and labelled as such.
* Status is ``complete`` only when the type and date are as expected, the
  layout is known, every required value parsed, ranks form a valid sequence,
  the row count equals the footer count and the grosses sum exactly to the
  footer total. Otherwise ``partial`` (data missing) or ``malformed`` (data
  inconsistent or layout wrong), or ``wrong_chart`` (a daily page where a
  weekend was expected, a different date). Only ``complete`` is ``verified``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from faramir.identity import classify_engagement, implied_opening, parse_slug

PARSER_VERSION = "tn-chart-1"
BASE_URL = "https://www.the-numbers.com/box-office-chart"
DAILY, WEEKEND = "daily", "weekend"

# Normalized header (whitespace removed, lower case) -> field name.
HEADERS = {
    "rank": "rank", "prev": "prev", "title": "title", "gross": "gross",
    "dailychange": "daily_change", "weeklychange": "weekly_change", "theaters": "theaters",
    "theateraverage": "theater_avg", "totalgross": "total_gross", "daysinrelease": "days_in_release",
}
COMMON_REQUIRED = ["rank", "prev", "title", "gross", "weekly_change", "theaters", "theater_avg",
                   "total_gross", "days_in_release"]
LAYOUT = {WEEKEND: COMMON_REQUIRED, DAILY: COMMON_REQUIRED + ["daily_change"]}
# Values every film row must carry for the chart to be complete.
REQUIRED_VALUES = ("gross", "theaters", "total_gross", "days_in_release")

HEADING_RE = re.compile(
    r"^(Weekend|Daily) Domestic Box Office\s+(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
    r"([A-Z][a-z]+ \d{1,2}, \d{4})$")
MONEY_RE = re.compile(r"^\$(\d{1,3}(?:,\d{3})+|\d+)$")
INT_RE = re.compile(r"^(\d{1,3}(?:,\d{3})+|\d+)$")  # TN groups thousands ("36,477"); plain digits accepted
PCT_RE = re.compile(r"^([+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?)%$")
PREV_RE = re.compile(r"^\((\d+|new|-)\)$", re.I)
FOOTER_RE = re.compile(r"^Reporting movies:\s*(\d+)$", re.I)


# ── URLs ─────────────────────────────────────────────────────────────────

def weekend_friday(d: date) -> date:
    """The Friday that keys the weekend containing ``d`` (Fri–Sun → that Friday; Mon–Thu → the one before)."""
    return d - timedelta(days=(d.weekday() - 4) % 7)


def weekend_url(friday: date) -> str:
    """TN keys weekend charts by their Friday (/weekend/2026/10/16); the Sunday URL 404s."""
    if friday.weekday() != 4:
        raise ValueError(f"{friday} is not a Friday; use weekend_friday() to find the weekend's key")
    return f"{BASE_URL}/weekend/{friday:%Y/%m/%d}"


def daily_url(d: date) -> str:
    return f"{BASE_URL}/daily/{d:%Y/%m/%d}"


# ── result types ─────────────────────────────────────────────────────────

@dataclass
class Issue:
    code: str
    detail: str
    row: int | None = None   # 1-based film row, when row-specific


@dataclass
class ChartRow:
    position: int                       # 1-based order on the page
    title_reported: str
    tn_slug: str
    slug_year: int | None
    source_rank: int | None             # as printed by TN; None where TN shows "-"
    source_rank_text: str
    derived_rank: int | None            # Faramir's rank by gross (ties share), NOT a TN figure
    prev_rank_reported: int | None
    prev_status: str                    # ranked | new | unranked | missing | malformed
    gross: int | None
    daily_change_pct: float | None
    weekly_change_pct: float | None
    theaters: int | None
    theater_avg: int | None
    total_gross: int | None
    days_in_release: int | None
    # Re-release evidence (kept for Discovery; Phase 0 picks exclude re-releases). TN's days in
    # release run from the film's FIRST US release, so for a re-release they date the original
    # run, and the total gross covers every engagement.
    first_us_release: str | None        # chart end date − days in release + 1
    engagement: str                     # original | late_release | rerelease | unknown
    engagement_start: str | None        # = first_us_release for a first run; unknown (None) for a re-release
    total_gross_scope: str              # this_engagement | all_engagements | unknown
    raw: dict = field(default_factory=dict)


@dataclass
class Chart:
    source_url: str
    chart_type: str | None              # daily | weekend | None (unrecognized)
    reporting_date: str | None          # daily: the day; weekend: the Friday
    weekend_start: str | None
    weekend_end: str | None
    status: str                         # complete | partial | malformed | wrong_chart
    rows: list[ChartRow]
    reported_count: int | None
    reported_total: int | None
    parsed_count: int
    parsed_total: int | None            # sum of row grosses; None if any gross is missing
    issues: list[Issue]
    header: list[str]
    parser_version: str = PARSER_VERSION

    @property
    def verified(self) -> bool:
        return self.status == "complete"

    def content_hash(self) -> str:
        """Hash of the parsed content (not of the time it was parsed) — the basis for Increment 11's
        deterministic collection_id."""
        body = {"type": self.chart_type, "date": self.reporting_date, "count": self.reported_count,
                "total": self.reported_total,
                "rows": [{k: v for k, v in asdict(r).items() if k != "raw"} for r in self.rows]}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verified"] = self.verified
        return d


# ── cell parsing (None + issue; never zero) ──────────────────────────────

def _text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ")).replace(" ", " ").strip()


def _norm_header(text: str) -> str:
    return re.sub(r"\s+", "", text.replace(" ", " ")).lower()


def _money(s: str) -> int | None:
    m = MONEY_RE.match(s)
    return int(m.group(1).replace(",", "")) if m else None


def _int(s: str) -> int | None:
    m = INT_RE.match(s)
    return int(m.group(1).replace(",", "")) if m else None


def _pct(s: str) -> float | None:
    m = PCT_RE.match(s)
    return float(m.group(1).replace(",", "")) if m else None


def _heading(soup) -> tuple[str | None, date | None, str]:
    for tag in soup.find_all("h1"):
        text = _text(tag)
        m = HEADING_RE.match(text)
        if m:
            return m.group(1).lower(), datetime.strptime(m.group(2), "%B %d, %Y").date(), text
    return None, None, ""


def _derived_ranks(grosses: list[int | None]) -> list[int | None]:
    """Competition ranking by gross (1, 2, 2, 4); rows without a gross get None."""
    known = sorted((g for g in grosses if g is not None), reverse=True)
    return [None if g is None else known.index(g) + 1 for g in grosses]


# ── the parser ───────────────────────────────────────────────────────────

def parse_chart(html: str, source_url: str, expected_type: str | None = None,
                expected_date: date | None = None) -> Chart:
    """Parse and classify one TN chart page. Never raises on page content."""
    soup = BeautifulSoup(html or "", "html.parser")
    issues: list[Issue] = []
    chart_type, chart_date, heading = _heading(soup)
    if chart_type is None:
        issues.append(Issue("heading_unrecognized", "no 'Weekend/Daily Domestic Box Office <date>' heading"))

    weekend_start = weekend_end = None
    if chart_type == WEEKEND and chart_date:
        if chart_date.weekday() != 4:
            issues.append(Issue("weekend_not_friday", f"weekend heading date {chart_date} is not a Friday"))
        weekend_start, weekend_end = chart_date, chart_date + timedelta(days=2)

    wrong = []
    if expected_type and chart_type and chart_type != expected_type:
        wrong.append(Issue("wrong_chart_type", f"expected a {expected_type} chart, page is a {chart_type} chart ({heading!r})"))
    if expected_date and chart_date and chart_date != expected_date:
        wrong.append(Issue("wrong_date", f"expected {expected_date}, page is for {chart_date}"))
    issues += wrong

    def result(status, rows=(), header=(), count=None, total=None, parsed_total=None):
        return Chart(source_url, chart_type, chart_date.isoformat() if chart_date else None,
                     weekend_start.isoformat() if weekend_start else None,
                     weekend_end.isoformat() if weekend_end else None,
                     status, list(rows), count, total, len(rows), parsed_total, issues, list(header))

    table = soup.find("table", class_="chart-desktop")
    if table is None:
        tables = soup.find_all("table")
        table = tables[0] if len(tables) == 1 else None  # never guess between several tables
        if table is None:
            issues.append(Issue("table_missing", f"no chart-desktop table ({len(tables)} other tables)"))
            return result("wrong_chart" if wrong else "malformed")
    trs = table.find_all("tr")
    if not trs:
        issues.append(Issue("table_empty", "chart table has no rows"))
        return result("wrong_chart" if wrong else "malformed")

    header_text = [_text(c) for c in trs[0].find_all(["th", "td"])]
    cols: dict[str, int] = {}
    for i, h in enumerate(header_text):
        name = HEADERS.get(_norm_header(h))
        if name is None:
            issues.append(Issue("unknown_column", f"unknown header {h!r} at position {i + 1}"))
        elif name in cols:
            issues.append(Issue("duplicate_column", f"header {h!r} appears twice"))
        else:
            cols[name] = i
    layout_type = chart_type or (expected_type if expected_type in LAYOUT else None)
    if layout_type:
        for name in LAYOUT[layout_type]:
            if name not in cols:
                issues.append(Issue("missing_column", f"required {layout_type} column {name!r} is absent"))
        extra = set(cols) - set(LAYOUT[layout_type])
        for name in sorted(extra):
            issues.append(Issue("unexpected_column", f"{name!r} does not belong on a {layout_type} chart"))
    layout_ok = not any(i.code in ("unknown_column", "duplicate_column", "missing_column", "unexpected_column")
                        for i in issues)
    if not layout_ok or "title" not in cols:
        return result("wrong_chart" if wrong else "malformed", header=header_text)

    width = len(header_text)
    rows: list[ChartRow] = []
    footer_count = footer_total = None
    end_date = weekend_end or chart_date
    for tr in trs[1:]:
        cells = tr.find_all(["td", "th"])
        texts = [_text(c) for c in cells]
        if not any(texts):
            continue  # spacer
        joined = " | ".join(texts)
        fm = next((FOOTER_RE.match(t) for t in texts if FOOTER_RE.match(t)), None)
        if fm:
            footer_count = int(fm.group(1))
            money = [m for m in (_money(t) for t in texts) if m is not None]
            footer_total = money[0] if len(money) == 1 else None
            if footer_total is None:
                issues.append(Issue("footer_total_unreadable", f"footer {joined!r}"))
            continue
        if len(cells) != width:
            issues.append(Issue("row_shape", f"{len(cells)} cells, header has {width}: {joined[:120]!r}", len(rows) + 1))
            continue
        n = len(rows) + 1

        def cell(name):
            return texts[cols[name]] if name in cols else ""

        a = cells[cols["title"]].find("a", href=True)
        slug, slug_year = parse_slug(a["href"]) if a else ("", None)
        title = cell("title")
        if not title:
            issues.append(Issue("missing_value", "title", n))
        if not slug:
            issues.append(Issue("missing_value", "title link / TN slug", n))

        rank_text = cell("rank")
        source_rank = _int(rank_text)
        if source_rank is None and rank_text != "-":
            issues.append(Issue("rank_malformed", f"rank cell {rank_text!r}", n))

        prev_text = cell("prev")
        pm = PREV_RE.match(prev_text)
        if not prev_text:
            prev_rank, prev_status = None, "missing"
        elif not pm:
            prev_rank, prev_status = None, "malformed"
            issues.append(Issue("prev_malformed", f"prev cell {prev_text!r}", n))
        elif pm.group(1).lower() == "new":
            prev_rank, prev_status = None, "new"
        elif pm.group(1) == "-":
            prev_rank, prev_status = None, "unranked"
        else:
            prev_rank, prev_status = int(pm.group(1)), "ranked"

        values = {"gross": _money(cell("gross")), "theaters": _int(cell("theaters")),
                  "theater_avg": _money(cell("theater_avg")), "total_gross": _money(cell("total_gross")),
                  "days_in_release": _int(cell("days_in_release"))}
        for name in REQUIRED_VALUES:
            if values[name] is None:
                code = "missing_value" if not cell(name) else "malformed_value"
                issues.append(Issue(code, f"{name} {cell(name)!r}", n))
        if values["theater_avg"] is None and cell("theater_avg"):
            issues.append(Issue("malformed_value", f"theater_avg {cell('theater_avg')!r}", n))
        changes = {}
        for name in ("daily_change", "weekly_change"):
            raw = cell(name)
            changes[name] = _pct(raw) if raw else None
            if raw and changes[name] is None:
                issues.append(Issue("malformed_value", f"{name} {raw!r}", n))
        if values["gross"] is not None and values["total_gross"] is not None and values["total_gross"] < values["gross"]:
            issues.append(Issue("inconsistent_value", f"total gross {values['total_gross']} < gross {values['gross']}", n))

        opening = implied_opening(end_date, values["days_in_release"]) if end_date and values["days_in_release"] else None
        engagement = classify_engagement(slug_year, values["days_in_release"], end_date) if end_date else "unknown"
        first_run = engagement in ("original", "late_release")
        rows.append(ChartRow(
            position=n, title_reported=title, tn_slug=slug, slug_year=slug_year,
            source_rank=source_rank, source_rank_text=rank_text, derived_rank=None,
            prev_rank_reported=prev_rank, prev_status=prev_status,
            gross=values["gross"], daily_change_pct=changes["daily_change"], weekly_change_pct=changes["weekly_change"],
            theaters=values["theaters"], theater_avg=values["theater_avg"], total_gross=values["total_gross"],
            days_in_release=values["days_in_release"], first_us_release=opening.isoformat() if opening else None,
            engagement=engagement, engagement_start=opening.isoformat() if opening and first_run else None,
            total_gross_scope="this_engagement" if first_run else ("all_engagements" if engagement == "rerelease" else "unknown"),
            raw={h: texts[i] for i, h in enumerate(header_text)},
        ))

    for r, dr in zip(rows, _derived_ranks([r.gross for r in rows])):
        r.derived_rank = dr

    # Ranks: 1..k contiguous from the top, then only "-"; printed ranks follow gross order.
    seen_unranked, expect = False, 1
    for r in rows:
        if r.source_rank is None:
            seen_unranked = seen_unranked or r.source_rank_text == "-"
            continue
        if seen_unranked:
            issues.append(Issue("rank_sequence", f"rank {r.source_rank} after an unranked row", r.position))
        elif r.source_rank != expect:
            issues.append(Issue("rank_sequence", f"rank {r.source_rank} where {expect} was expected", r.position))
        expect = r.source_rank + 1
    ranked = [r for r in rows if r.source_rank is not None and r.gross is not None]
    for a, b in zip(ranked, ranked[1:]):
        if b.gross > a.gross:
            issues.append(Issue("rank_order", f"rank {b.source_rank} out-grosses rank {a.source_rank}", b.position))

    grosses = [r.gross for r in rows]
    parsed_total = sum(grosses) if rows and all(g is not None for g in grosses) else None
    if not rows:
        issues.append(Issue("no_rows", "no film rows parsed"))
    if footer_count is None:
        issues.append(Issue("footer_missing", "no 'Reporting movies: N' footer; completeness cannot be verified"))
    else:
        if len(rows) < footer_count:
            issues.append(Issue("rows_missing", f"{len(rows)} rows parsed, footer reports {footer_count}"))
        elif len(rows) > footer_count:
            issues.append(Issue("rows_extra", f"{len(rows)} rows parsed, footer reports {footer_count}"))
        if footer_total is not None and parsed_total is not None and parsed_total != footer_total:
            if len(rows) < footer_count and parsed_total < footer_total:
                # Consistent with the missing rows: incomplete, not contradictory.
                issues.append(Issue("gross_short", f"rows sum to ${parsed_total:,}, ${footer_total - parsed_total:,} "
                                                   f"short of the footer total ${footer_total:,}"))
            else:
                issues.append(Issue("gross_mismatch", f"rows sum to ${parsed_total:,}, footer total is ${footer_total:,}"))

    partial_codes = {"missing_value", "rows_missing", "gross_short", "footer_missing", "no_rows", "footer_total_unreadable", "row_shape"}
    malformed_codes = {"heading_unrecognized", "weekend_not_friday", "rank_malformed", "rank_sequence", "rank_order",
                       "prev_malformed", "malformed_value", "inconsistent_value", "rows_extra", "gross_mismatch"}
    codes = {i.code for i in issues}
    if wrong:
        status = "wrong_chart"
    elif codes & malformed_codes:
        status = "malformed"
    elif codes & partial_codes:
        status = "partial"
    else:
        status = "complete"
    return result(status, rows, header_text, footer_count, footer_total, parsed_total)
