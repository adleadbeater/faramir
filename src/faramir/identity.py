"""Active-film identity resolution: which TMDB film is this chart row? (P0-2)

A title match alone never establishes identity. Each chart row is resolved in
this order; the first rule that applies decides:

0. **Re-release.** The TN slug year is more than one year before the chart
   year ("Old-Classic-(1985)" on a 2026 chart). Excluded from picks and not
   tracked; nothing is looked up.
1. **Editor override, by TN slug** (``identity_overrides`` tab). Manual.
2. **Existing state row.** Matched by stored TN slug; or, for a legacy row
   that has no slug yet, by normalized title *and* a compatible year. The
   row's ``tmdb_id_override`` (editor column) wins over its ``tmdb_id``.
3. **TMDB search.** Exact normalized title, release year within the slug
   year ±1, release date within 60 days of the implied opening (chart date
   minus days in release; a US theatrical date may satisfy this), exactly one
   surviving candidate, and an IMDb ID on that candidate.
4. Otherwise **unresolved**: skipped, reported with its reason and the
   candidates that were considered — never guessed.

Remakes are separated by year; sequels by the exact title. Pure logic: the
TMDB calls are passed in, so this module is testable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from faramir.bom_fetch import normalize_title

RELEASE_WINDOW_DAYS = 60
SLUG_YEAR_TOLERANCE = 1
MAX_RELEASE_DATE_LOOKUPS = 3

SLUG_RE = re.compile(r"/movie/([^/#?]+)")
# "(2026)", "(2014-Japan)", "(1971-United-Kingdom)": year, then an optional country/qualifier.
SLUG_YEAR_RE = re.compile(r"\((\d{4})(?:-[A-Za-z0-9-]+)?\)$")
# TN's "Days in Release" counts from a film's first US release, so a film still charting
# more than this long after it is a revival / re-release engagement [CAL].
REVIVAL_DAYS = 365


def parse_slug(href: str | None) -> tuple[str, int | None]:
    """'/movie/Beta-Story-(2026)#tab=summary' -> ('Beta-Story-(2026)', 2026)."""
    m = SLUG_RE.search(href or "")
    if not m:
        return "", None
    slug = m.group(1)
    y = SLUG_YEAR_RE.search(slug)
    return slug, int(y.group(1)) if y else None


def implied_opening(chart_date: date, days_in_release: int) -> date | None:
    return chart_date - timedelta(days=days_in_release - 1) if days_in_release and days_in_release > 0 else None


def is_rerelease(slug_year: int | None, chart_date: date) -> bool:
    """The Phase 0 exclusion rule (P0-2): slug year more than one year before the chart year."""
    return slug_year is not None and slug_year < chart_date.year - 1


def classify_engagement(slug_year: int | None, days_in_release: int | None, chart_end: date) -> str:
    """What kind of theatrical engagement a chart row is.

    * ``rerelease`` — still charting more than REVIVAL_DAYS after its first US release (TN's day
      count runs from that first release; e.g. Avengers: Endgame at 2,733 days), or, with no day
      count, an old slug year;
    * ``late_release`` — an older film (slug year) in its first US run (e.g. a 2024 French film at
      59 days);
    * ``original`` — its first run, released around its slug year;
    * ``unknown`` — not enough evidence.
    """
    if days_in_release:
        if days_in_release > REVIVAL_DAYS:
            return "rerelease"
        if slug_year is None:
            return "unknown"
        opening = implied_opening(chart_end, days_in_release)
        return "late_release" if slug_year < opening.year - 1 else "original"
    if slug_year is None:
        return "unknown"
    return "rerelease" if is_rerelease(slug_year, chart_end) else "original"


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _year(value) -> int | None:
    m = re.match(r"(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class Overrides:
    """Editor overrides by TN slug. A slug with conflicting or malformed rows is never resolved from
    the tab: it maps to an OverrideConflict and the film stays unresolved until an editor fixes it."""
    by_slug: dict = field(default_factory=dict)          # slug -> int | OverrideConflict
    problems: list[str] = field(default_factory=list)    # diagnostics for the run report and header

    def get(self, slug):
        return self.by_slug.get(slug)

    def __contains__(self, slug):
        return slug in self.by_slug


@dataclass
class OverrideConflict:
    slug: str
    values: list[str]
    reason: str


def parse_overrides(values: list[list]) -> Overrides:
    """Parse the editor-owned identity_overrides tab (tn_slug, tmdb_id, note).

    * identical duplicate rows for a slug: one mapping (noted);
    * different tmdb_ids for a slug, or a malformed tmdb_id on a row with a slug: conflict — the
      slug resolves to nothing and the reason is reported;
    * a row with a tmdb_id but no slug, or an unknown header: reported, ignored.
    """
    out = Overrides()
    if not values:
        return out
    header = [str(h).strip() for h in values[0]]
    if "tn_slug" not in header or "tmdb_id" not in header:
        out.problems.append(f"identity_overrides header {header} lacks tn_slug/tmdb_id — tab ignored")
        return out
    seen: dict[str, list[tuple[int, str]]] = {}
    for n, r in enumerate(values[1:], start=2):
        r = [str(c) for c in r] + [""] * len(header)
        slug, tid = r[header.index("tn_slug")].strip(), r[header.index("tmdb_id")].strip()
        if not slug and not tid:
            continue
        if not slug:
            out.problems.append(f"identity_overrides row {n}: tmdb_id {tid!r} without a tn_slug — ignored")
            continue
        seen.setdefault(slug, []).append((n, tid))
    for slug, rows in seen.items():
        raw = [t for _, t in rows]
        bad = [f"row {n}: {t!r}" for n, t in rows if not t.isdigit()]
        distinct = sorted({t for t in raw if t.isdigit()}, key=int)
        if bad:
            out.by_slug[slug] = OverrideConflict(slug, raw, f"malformed tmdb_id ({'; '.join(bad)})")
        elif len(distinct) > 1:
            out.by_slug[slug] = OverrideConflict(slug, raw, f"conflicting tmdb_ids {distinct} "
                                                            f"(rows {', '.join(str(n) for n, _ in rows)})")
        else:
            out.by_slug[slug] = int(distinct[0])
            if len(rows) > 1:
                out.problems.append(f"identity_overrides: {slug} listed {len(rows)} times with the same tmdb_id "
                                    f"{distinct[0]} (rows {', '.join(str(n) for n, _ in rows)}) — used once")
    for c in out.by_slug.values():
        if isinstance(c, OverrideConflict):
            out.problems.append(f"identity_overrides: {c.slug} not used — {c.reason}")
    return out


@dataclass
class ChartEntry:
    title: str
    tn_slug: str
    slug_year: int | None
    days_in_release: int
    chart_date: date

    @property
    def opening(self) -> date | None:
        return implied_opening(self.chart_date, self.days_in_release)

    @property
    def target_year(self) -> int | None:
        return self.slug_year or (self.opening.year if self.opening else None)


@dataclass
class Identity:
    title: str
    tn_slug: str
    status: str                          # resolved | unresolved | rerelease
    tmdb_id: int | None = None
    method: str = ""                     # override | state_override | state_slug | state_legacy_title_year | search
    confidence: str = ""                 # manual | high | medium | legacy
    reason: str = ""
    state_row: dict | None = None        # the existing state row this chart row continues
    details: dict | None = None          # TMDB details when a new row (or re-key) is needed
    considered: list[dict] = field(default_factory=list)
    engagement: dict | None = None       # re-release evidence, kept for Discovery (never used for picks)

    @property
    def identity_class(self) -> str:
        """verified (search) | manual (override) | legacy (pre-Increment-9 title+year) | unresolved | rerelease."""
        if self.status != "resolved":
            return self.status
        return {"high": "verified", "medium": "verified", "manual": "manual"}.get(self.confidence, "legacy")

    def report(self) -> dict:
        return {"title": self.title, "tn_slug": self.tn_slug, "status": self.status, "tmdb_id": self.tmdb_id,
                "method": self.method, "confidence": self.confidence, "identity_class": self.identity_class,
                "reason": self.reason, "state_tmdb_id": _int((self.state_row or {}).get("tmdb_id")),
                "engagement": self.engagement, "considered": self.considered}


def _year_compatible(row: dict, entry: ChartEntry) -> tuple[bool, str]:
    rd = row.get("release_date")
    full, year = _date(rd), _year(rd)
    if year is None:
        return False, "state row has no release date"
    if entry.slug_year is not None:
        ok = abs(year - entry.slug_year) <= SLUG_YEAR_TOLERANCE
        return ok, f"state row year {year} vs slug year {entry.slug_year}"
    if entry.opening and full:
        ok = abs((full - entry.opening).days) <= RELEASE_WINDOW_DAYS
        return ok, f"state row released {full} vs implied opening {entry.opening}"
    if entry.opening:
        ok = abs(year - entry.opening.year) <= SLUG_YEAR_TOLERANCE
        return ok, f"state row year {year} vs implied opening {entry.opening}"
    return False, "no slug year or days in release to compare"


def match_state_row(entry: ChartEntry, rows: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """(row, method, considered). method '' with row None = no match (considered says why)."""
    considered: list[dict] = []
    if entry.tn_slug:
        by_slug = [r for r in rows if str(r.get("tn_slug") or "") == entry.tn_slug]
        if len(by_slug) == 1:
            return by_slug[0], "state_slug", considered
        if len(by_slug) > 1:
            ids = [r.get("tmdb_id") for r in by_slug]
            return None, "ambiguous_state", [{"tmdb_id": i, "rejected": "several state rows share this slug"} for i in ids]
    norm = normalize_title(entry.title)
    legacy = []
    for r in rows:
        if r.get("tn_slug"):
            continue  # a row with a different slug is a different film, whatever its title
        if norm not in {normalize_title(str(r.get("bom_title_seen") or "")), normalize_title(str(r.get("canonical_title") or ""))}:
            continue
        ok, why = _year_compatible(r, entry)
        if ok:
            legacy.append(r)
        else:
            considered.append({"tmdb_id": r.get("tmdb_id"), "title": r.get("canonical_title"),
                               "rejected": f"same title, different film: {why}"})
    if len(legacy) == 1:
        return legacy[0], "state_legacy_title_year", considered
    if len(legacy) > 1:
        return None, "ambiguous_state", considered + [
            {"tmdb_id": r.get("tmdb_id"), "rejected": "several legacy state rows match title and year"} for r in legacy]
    return None, "", considered


def _us_theatrical_dates(release_dates: dict) -> list[date]:
    out = []
    for country in release_dates.get("results", []):
        if country.get("iso_3166_1") != "US":
            continue
        for rd in country.get("release_dates", []):
            if rd.get("type") in (2, 3):  # limited, theatrical
                d = _date(rd.get("release_date"))
                if d:
                    out.append(d)
    return out


def search_identity(entry: ChartEntry, search: Callable[[str, int | None], list[dict]],
                    release_dates: Callable[[int], dict]) -> Identity:
    ident = Identity(entry.title, entry.tn_slug, "unresolved", method="search")
    year = entry.target_year
    if year is None:
        ident.reason = "no_year_evidence: no slug year and no days in release"
        return ident
    seen: dict[int, dict] = {}
    for y in (year, None):
        for r in search(entry.title, y):
            if r.get("id") is not None:
                seen.setdefault(int(r["id"]), r)
    norm = normalize_title(entry.title)
    passing, window_only, other_titles = [], [], 0
    for tid, r in seen.items():
        titles = {normalize_title(str(r.get("title") or "")), normalize_title(str(r.get("original_title") or ""))}
        if norm not in titles:
            other_titles += 1  # sequels, prefixes, unrelated films
            continue
        rd = _date(r.get("release_date"))
        row = {"tmdb_id": tid, "title": r.get("title"), "release_date": r.get("release_date") or ""}
        if rd is None:
            ident.considered.append({**row, "rejected": "no release date"})
        elif abs(rd.year - year) > SLUG_YEAR_TOLERANCE:
            ident.considered.append({**row, "rejected": f"year {rd.year} outside {year}±{SLUG_YEAR_TOLERANCE} (remake/original?)"})
        elif entry.opening and abs((rd - entry.opening).days) > RELEASE_WINDOW_DAYS:
            window_only.append((tid, row, rd))
        else:
            passing.append((tid, row, "primary_release_date"))
    if not passing and window_only and entry.opening:
        for tid, row, rd in window_only[:MAX_RELEASE_DATE_LOOKUPS]:
            try:
                us = _us_theatrical_dates(release_dates(tid))
            except Exception as exc:  # a failed lookup leaves it unresolved, never guessed
                ident.considered.append({**row, "rejected": f"release dates lookup failed: {type(exc).__name__}"})
                continue
            if any(abs((d - entry.opening).days) <= RELEASE_WINDOW_DAYS for d in us):
                passing.append((tid, row, "us_release_date"))
            else:
                ident.considered.append({**row, "rejected": f"released {rd}, not within {RELEASE_WINDOW_DAYS} days of "
                                                            f"implied opening {entry.opening} (possible re-release)"})
    else:
        for tid, row, rd in window_only:
            ident.considered.append({**row, "rejected": f"released {rd}, not within {RELEASE_WINDOW_DAYS} days of "
                                                        f"implied opening {entry.opening}"})
    for tid, row, how in passing:
        ident.considered.append({**row, "accepted_by": how})
    if other_titles:
        ident.considered.append({"rejected": f"{other_titles} search result(s) with a different title"})
    if len(passing) > 1:
        ident.reason = f"ambiguous: {len(passing)} films match title, year and release window"
        return ident
    if not passing:
        ident.reason = "no_match: no TMDB film matches title, year and release window"
        return ident
    tid, _, how = passing[0]
    ident.tmdb_id = tid
    ident.confidence = "high" if entry.slug_year is not None and how == "primary_release_date" else "medium"
    return ident


def resolve(entry: ChartEntry, state_rows: list[dict], overrides,
            search: Callable[[str, int | None], list[dict]], details: Callable[[int], dict],
            release_dates: Callable[[int], dict]) -> Identity:
    if is_rerelease(entry.slug_year, entry.chart_date):
        kind = classify_engagement(entry.slug_year, entry.days_in_release, entry.chart_date)
        what = "late US release of an older film" if kind == "late_release" else "re-release"
        ident = Identity(entry.title, entry.tn_slug, "rerelease", reason=(
            f"rerelease: slug year {entry.slug_year} is before {entry.chart_date.year - 1} ({what}); "
            "excluded from Phase 0 picks"))
        ident.engagement = {"kind": kind, "original_identity": entry.tn_slug, "slug_year": entry.slug_year,
                            "days_in_release": entry.days_in_release or None,
                            "first_us_release": entry.opening.isoformat() if entry.opening else None}
        return ident

    row, how, considered = match_state_row(entry, state_rows)
    if how == "ambiguous_state":
        return Identity(entry.title, entry.tn_slug, "unresolved", reason="ambiguous_state: several state rows match",
                        considered=considered)

    def need_details(ident: Identity) -> Identity:
        try:
            ident.details = details(ident.tmdb_id)
        except Exception as exc:
            return Identity(entry.title, entry.tn_slug, "unresolved", method=ident.method, state_row=row,
                            reason=f"details_failed: TMDB {ident.tmdb_id}: {type(exc).__name__}", considered=ident.considered)
        if not ident.details.get("imdb_id"):
            return Identity(entry.title, entry.tn_slug, "unresolved", method=ident.method, state_row=row,
                            reason=f"no_imdb_id: TMDB {ident.tmdb_id} has no IMDb ID", considered=ident.considered)
        ident.status = "resolved"
        return ident

    tab_override = overrides.get(entry.tn_slug) if entry.tn_slug else None
    if isinstance(tab_override, OverrideConflict):
        return Identity(entry.title, entry.tn_slug, "unresolved", method="override", state_row=row,
                        reason=f"override_conflict: identity_overrides {tab_override.reason} — fix the tab; "
                               "no identity is guessed", considered=considered)
    if tab_override:
        ident = Identity(entry.title, entry.tn_slug, "unresolved", tmdb_id=tab_override, method="override",
                         confidence="manual", state_row=row, considered=considered)
        if row and _int(row.get("tmdb_id")) == tab_override:
            ident.status = "resolved"
            return ident
        return need_details(ident)

    if row is not None:
        raw_override = str(row.get("tmdb_id_override") or "").strip()
        if raw_override and _int(raw_override) is None:
            return Identity(entry.title, entry.tn_slug, "unresolved", method="state_override", state_row=row,
                            reason=f"override_malformed: state tmdb_id_override {raw_override!r} is not a TMDB id — "
                                   "fix the cell; no identity is guessed", considered=considered)
        row_override = _int(row.get("tmdb_id_override"))
        if row_override and row_override != _int(row.get("tmdb_id")):
            ident = Identity(entry.title, entry.tn_slug, "unresolved", tmdb_id=row_override, method="state_override",
                             confidence="manual", state_row=row, considered=considered)
            return need_details(ident)
        confidence = str(row.get("identity_confidence") or "") or "legacy"
        method = str(row.get("identity_method") or "") if how == "state_slug" else how
        return Identity(entry.title, entry.tn_slug, "resolved", tmdb_id=_int(row.get("tmdb_id")),
                        method=method or how, confidence="manual" if row_override else confidence,
                        state_row=row, considered=considered)

    ident = search_identity(entry, search, release_dates)
    ident.considered = considered + ident.considered
    if ident.tmdb_id is None:
        return ident
    return need_details(ident)


def dedupe(identities: list[Identity]) -> None:
    """Two chart rows resolving to one TMDB film: keep the one continuing a state row, if exactly
    one does; otherwise none of them (never guess which row is the film)."""
    by_id: dict[int, list[Identity]] = {}
    for i in identities:
        if i.status == "resolved":
            by_id.setdefault(i.tmdb_id, []).append(i)
    for tid, group in by_id.items():
        if len(group) < 2:
            continue
        keep = [i for i in group if i.state_row is not None and _int(i.state_row.get("tmdb_id")) == tid]
        for i in group:
            if len(keep) == 1 and i is keep[0]:
                continue
            i.status = "unresolved"
            i.reason = f"duplicate_identity: {len(group)} chart rows resolve to TMDB {tid}"
