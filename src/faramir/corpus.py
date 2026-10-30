"""Corpus loading, health classification and search utilities for Faramir.

The daily pipeline loads the schema-v2 corpus (``data/corpus.v2.json``) through
``load_corpus_v2``. A gross is either a reported int or ``None`` (status
``missing``/``confirmed_zero``); a missing gross is never read as zero.

Health classes (thresholds marked [CAL] need calibration):

ABORT — normal operation is unsafe; no picks are made and an alert is sent.
    Any of: file missing/unreadable/not JSON; legacy v1 list format; schema
    version != 2; meta missing or unparseable ``gross_as_of``; ``gross_as_of``
    in the future; meta row count or coverage disagreeing with the rows;
    duplicate ``imdb_id``; duplicate verified ``tmdb_id``; invalid rows > 1%
    [CAL]; unresolved identities > 2% [CAL]; fewer than 1,800 usable rows
    [CAL]; worldwide reported share < 95% [CAL].
    Domestic coverage is NOT an abort criterion: ~49.7% is the known limit of
    the TMDB-only corpus, enforced per comparison instead.

DEGRADED — identifiable bad records are excluded and the rest is reliable;
    picks are allowed from usable records only, with an editor warning.
    Any of (and no ABORT): 0 < invalid rows <= 1%; 0 < unresolved identities
    <= 2%; corpus older than 45 days [CAL].

OK — none of the above.

Stale comparisons (``stale_evidence``) are excluded per record and do not
change the class: a record is stale only when there is verifiable evidence its
gross moved after ``gross_as_of`` — see that function for the exact rule.
"""

from __future__ import annotations

import bisect
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from faramir.corpus_validate import METRICS, SCHEMA_VERSION, coverage, row_problems

logger = logging.getLogger(__name__)

OK, DEGRADED, ABORT = "OK", "DEGRADED", "ABORT"

ABORT_INVALID_SHARE = 0.01
ABORT_UNRESOLVED_SHARE = 0.02
ABORT_MIN_USABLE_ROWS = 1800
ABORT_MIN_WW_REPORTED_SHARE = 0.95
DEGRADED_MAX_AGE_DAYS = 45
STATE_CHART_LAG_DAYS = 2  # state last_seen is the run date; chart data is up to 2 days older
TITLE_EVIDENCE_MIN_RATIO = 0.9
TITLE_EVIDENCE_MAX_RATIO_OLD = 1.5  # [CAL]


def load_corpus(path: str = "data/corpus.json") -> list[dict]:
    """Load the legacy v1 corpus list. Not used by the v2 daily pipeline."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        logger.info("Loaded %d films from corpus at %s", len(data), path)
        return data
    except FileNotFoundError:
        logger.warning("Corpus file not found at %s, returning empty list", path)
        return []
    except json.JSONDecodeError as exc:
        logger.error("Corpus JSON decode error: %s", exc)
        return []


def reported(film: dict, metric: str) -> int | None:
    """The film's gross for ``metric`` if it is a reported positive int, else None.

    v1 rows (no status field) treat a truthy int as reported, matching the
    legacy behavior; ``None``/missing is never coerced to zero.
    """
    value = film.get(f"{metric}_lifetime")
    status = film.get(f"{metric}_status")
    if status is not None and status != "reported":
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


# ── v2 loading and health ────────────────────────────────────────────────

@dataclass
class Exclusion:
    imdb_id: str
    title: str
    reason: str
    detail: str = ""


@dataclass
class CorpusLoad:
    status: str
    reasons: list[str]
    meta: dict = field(default_factory=dict)
    films: list[dict] = field(default_factory=list)  # usable records only
    exclusions: list[Exclusion] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    age_days: int | None = None

    def exclusion_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.exclusions:
            counts[e.reason] = counts.get(e.reason, 0) + 1
        return counts

    def editor_warning(self) -> str | None:
        """One Slack-header line for DEGRADED; None when OK. ABORT posts no picks."""
        if self.status != DEGRADED:
            return None
        parts = []
        counts = self.exclusion_counts()
        bad = {k: v for k, v in counts.items() if k != "stale"}
        if bad:
            parts.append(f"{sum(bad.values())} comparison record(s) excluded ({', '.join(f'{k}: {v}' for k, v in sorted(bad.items()))})")
        if self.age_days is not None and self.age_days > DEGRADED_MAX_AGE_DAYS:
            parts.append(f"comparison grosses are as of {self.meta.get('gross_as_of')} ({self.age_days} days old)")
        return "⚠ Comparison data degraded: " + "; ".join(parts) + ". Verify comparison figures before assigning."

    def apply_stale(
        self,
        state_rows: list[dict] | None,
        chart_rows: list[dict] | None,
        chart_date: date | None,
    ) -> int:
        """Move records with stale-gross evidence from ``films`` to exclusions.

        Stale exclusions never change the health class. Returns the count.
        """
        if self.status == ABORT or not self.films:
            return 0
        gross_as_of = date.fromisoformat(str(self.meta["gross_as_of"]))
        stale = stale_evidence(self.films, gross_as_of, state_rows, chart_rows, chart_date)
        if not stale:
            return 0
        kept = []
        for f in self.films:
            if f["imdb_id"] in stale:
                self.exclusions.append(Exclusion(f["imdb_id"], str(f.get("title", "")), "stale", stale[f["imdb_id"]]))
            else:
                kept.append(f)
        self.films = kept
        return len(stale)

    def summary(self) -> dict:
        return {
            "status": self.status,
            "reasons": self.reasons,
            "usable_rows": len(self.films),
            "exclusions": self.exclusion_counts(),
            "coverage": self.coverage,
            "gross_as_of": self.meta.get("gross_as_of"),
            "age_days": self.age_days,
        }


def _abort(reason: str, meta: dict | None = None) -> CorpusLoad:
    logger.error("Corpus ABORT: %s", reason)
    return CorpusLoad(status=ABORT, reasons=[reason], meta=meta or {})


def _norm_title(title: str) -> str:
    t = (title or "").lower().strip()
    for prefix in ("the ", "a ", "an "):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return re.sub(r"[^a-z0-9 ]", "", t).strip()


def stale_evidence(
    films: list[dict],
    gross_as_of: date,
    state_rows: list[dict] | None = None,
    chart_rows: list[dict] | None = None,
    chart_date: date | None = None,
) -> dict[str, str]:
    """Return {imdb_id: rule} for records with verifiable evidence their gross
    changed after ``gross_as_of``. Release-date proximity alone is never used.

    S1 (identity evidence): a Faramir state row with the same imdb_id (or the
        same tmdb_id when the state row has no imdb_id) was seen on a chart
        dated after ``gross_as_of``. ``last_seen`` is the run date, so the
        chart date is taken as ``last_seen - 2 days`` (the D-2 fallback);
        this can miss one day of evidence but never invents it.
    S2 (title evidence): the current daily chart (dated after ``gross_as_of``)
        lists a title whose normalized form equals the record's AND whose
        cumulative domestic gross is consistent with being the same film:
        at least 90% of the record's reported domestic gross, and — for a
        record released before the chart's previous year (a re-release
        candidate) — at most 150% of it [CAL]. A much larger total on an old
        title is a different, newer film sharing the name (e.g. a remake), not
        evidence. Recent records (released in the chart year or the year
        before) have no upper bound: the same film can grow a lot after the
        gross date, and S1 normally catches those first. If the record's
        domestic gross is missing, title equality alone is not evidence and S2
        does not apply.
    """
    stale: dict[str, str] = {}
    by_imdb = {f["imdb_id"]: f for f in films}
    by_tmdb = {f.get("tmdb_id"): f for f in films if f.get("tmdb_id") is not None}

    for row in state_rows or []:
        try:
            seen = date.fromisoformat(str(row.get("last_seen", ""))[:10])
        except ValueError:
            continue
        if seen - timedelta(days=STATE_CHART_LAG_DAYS) <= gross_as_of:
            continue
        film = by_imdb.get(str(row.get("imdb_id") or "")) if row.get("imdb_id") else None
        if film is None and not row.get("imdb_id"):
            try:
                film = by_tmdb.get(int(row.get("tmdb_id")))
            except (TypeError, ValueError):
                film = None
        if film is not None:
            stale.setdefault(film["imdb_id"], "S1_tracked_on_chart_after_gross_date")

    if chart_rows and chart_date and chart_date > gross_as_of:
        by_title: dict[str, list[dict]] = {}
        for f in films:
            by_title.setdefault(_norm_title(f.get("title", "")), []).append(f)
        for row in chart_rows:
            current = int(row.get("gross_to_date") or 0)
            for f in by_title.get(_norm_title(row.get("title", "")), []):
                dom = reported(f, "domestic")
                if not dom or current < TITLE_EVIDENCE_MIN_RATIO * dom:
                    continue
                try:
                    old_title = int(f.get("release_year") or 0) < chart_date.year - 1
                except (TypeError, ValueError):
                    old_title = True  # unknown year: apply the stricter bound
                if old_title and current > TITLE_EVIDENCE_MAX_RATIO_OLD * dom:
                    continue
                stale.setdefault(f["imdb_id"], "S2_title_on_chart_after_gross_date")
    return stale


def load_corpus_v2(
    path: str,
    run_date: date,
    state_rows: list[dict] | None = None,
    chart_rows: list[dict] | None = None,
    chart_date: date | None = None,
) -> CorpusLoad:
    """Load, validate and classify the v2 corpus. Never falls back to v1."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _abort(f"corpus_missing: {path} not found")
    except (OSError, json.JSONDecodeError) as exc:
        return _abort(f"corpus_unreadable: {type(exc).__name__}: {exc}")

    if isinstance(data, list):
        return _abort(
            "legacy_format: corpus is a v1 list (legacy full build output); "
            "the v2 pipeline requires a schema-v2 file from build_corpus.py --repair-identities"
        )
    if not isinstance(data, dict) or not isinstance(data.get("films"), list) or not isinstance(data.get("meta"), dict):
        return _abort("bad_format: expected {'meta': {...}, 'films': [...]}")

    meta, films = data["meta"], data["films"]
    if meta.get("corpus_schema_version") != SCHEMA_VERSION:
        return _abort(f"schema_version: expected {SCHEMA_VERSION}, got {meta.get('corpus_schema_version')!r}", meta)
    try:
        gross_as_of = date.fromisoformat(str(meta.get("gross_as_of")))
    except ValueError:
        return _abort(f"meta_missing: gross_as_of {meta.get('gross_as_of')!r} is not a date", meta)
    if gross_as_of > run_date:
        return _abort(f"gross_as_of_in_future: {gross_as_of} > {run_date}", meta)
    actual_cov = coverage(films)
    if meta.get("rows") != len(films) or meta.get("coverage") != actual_cov:
        return _abort("meta_mismatch: recorded row count/coverage disagree with the rows (file edited or truncated?)", meta)

    reasons: list[str] = []
    seen_imdb: dict[str, int] = {}
    seen_tmdb: dict[int, int] = {}
    for f in films:
        seen_imdb[f.get("imdb_id")] = seen_imdb.get(f.get("imdb_id"), 0) + 1
        if f.get("identity_status") == "verified":
            seen_tmdb[f.get("tmdb_id")] = seen_tmdb.get(f.get("tmdb_id"), 0) + 1
    dup_imdb = [k for k, n in seen_imdb.items() if k and n > 1]
    dup_tmdb = [k for k, n in seen_tmdb.items() if n > 1]
    if dup_imdb:
        reasons.append(f"duplicate_imdb_id: {len(dup_imdb)} id(s), e.g. {dup_imdb[:3]}")
    if dup_tmdb:
        reasons.append(f"duplicate_tmdb_id: {len(dup_tmdb)} id(s), e.g. {dup_tmdb[:3]}")
    if reasons:
        return CorpusLoad(status=ABORT, reasons=reasons, meta=meta, coverage=actual_cov)

    exclusions: list[Exclusion] = []
    usable: list[dict] = []
    n_invalid = n_unresolved = 0
    for f in films:
        problems = row_problems(f)
        if problems:
            n_invalid += 1
            exclusions.append(Exclusion(str(f.get("imdb_id")), str(f.get("title", "")), "invalid", "; ".join(problems)))
        elif f.get("identity_status") == "unresolved":
            n_unresolved += 1
            exclusions.append(Exclusion(f["imdb_id"], str(f.get("title", "")), "unresolved_identity"))
        else:
            usable.append(f)

    total = len(films) or 1
    ww_share = actual_cov["worldwide"]["reported_share"]
    if n_invalid / total > ABORT_INVALID_SHARE:
        reasons.append(f"invalid_share: {n_invalid}/{len(films)} rows invalid (> {ABORT_INVALID_SHARE:.0%})")
    if n_unresolved / total > ABORT_UNRESOLVED_SHARE:
        reasons.append(f"unresolved_share: {n_unresolved}/{len(films)} unresolved (> {ABORT_UNRESOLVED_SHARE:.0%})")
    if len(usable) < ABORT_MIN_USABLE_ROWS:
        reasons.append(f"too_few_usable_rows: {len(usable)} < {ABORT_MIN_USABLE_ROWS}")
    if ww_share < ABORT_MIN_WW_REPORTED_SHARE:
        reasons.append(f"worldwide_coverage: {ww_share:.1%} < {ABORT_MIN_WW_REPORTED_SHARE:.0%}")
    age_days = (run_date - gross_as_of).days
    if reasons:
        for r in reasons:
            logger.error("Corpus ABORT: %s", r)
        return CorpusLoad(ABORT, reasons, meta, [], exclusions, actual_cov, age_days)

    if n_invalid:
        reasons.append(f"invalid_rows_excluded: {n_invalid}")
    if n_unresolved:
        reasons.append(f"unresolved_rows_excluded: {n_unresolved}")
    if age_days > DEGRADED_MAX_AGE_DAYS:
        reasons.append(f"corpus_age: {age_days} days > {DEGRADED_MAX_AGE_DAYS}")
    status = DEGRADED if reasons else OK
    load = CorpusLoad(status, reasons, meta, usable, exclusions, actual_cov, age_days)
    if state_rows or chart_rows:
        load.apply_stale(state_rows, chart_rows, chart_date)
    logger.info("Corpus %s: %d usable of %d; exclusions %s; coverage dom %.1f%% ww %.1f%%",
                status, len(usable), len(films), load.exclusion_counts(),
                100 * actual_cov["domestic"]["reported_share"], 100 * ww_share)
    return load


# ── search ───────────────────────────────────────────────────────────────

def build_index(corpus: list[dict]) -> dict:
    """Build sorted indexes for binary search.

    A film is indexed on an axis only when that gross is reported, so a film
    with a missing domestic gross still takes part in worldwide comparisons.

    Returns:
        {
            domestic_index: list sorted by domestic_lifetime ascending,
            worldwide_index: list sorted by worldwide_lifetime ascending,
        }
    """
    out = {}
    for m in METRICS:
        out[f"{m}_index"] = sorted(
            [f for f in corpus if reported(f, m) is not None],
            key=lambda f, m=m: f[f"{m}_lifetime"],
        )
    return out


def films_in_range(index: dict, axis: str, lo: int, hi: int) -> list[dict]:
    """Return films with lifetime gross in [lo, hi] on the given axis.

    Uses binary search on the pre-sorted index list.
    axis: 'domestic' or 'worldwide'
    """
    key = f"{axis}_index"
    sorted_list = index.get(key, [])
    if not sorted_list:
        return []

    field_name = f"{axis}_lifetime"
    values = [f[field_name] for f in sorted_list]

    lo_pos = bisect.bisect_left(values, lo)
    hi_pos = bisect.bisect_right(values, hi)
    return sorted_list[lo_pos:hi_pos]


def top_n_by_genre(corpus: list[dict], genre: str, n: int = 10, axis: str = "worldwide") -> list[dict]:
    """Return top N films in corpus matching genre, sorted by axis lifetime desc (reported grosses only)."""
    field_name = f"{axis}_lifetime"
    matches = [
        f for f in corpus
        if genre.lower() in [g.lower() for g in f.get("genres", [])]
        and reported(f, axis) is not None
    ]
    return sorted(matches, key=lambda f: f[field_name], reverse=True)[:n]
