"""Integrity gates and coverage for the schema-v2 comparison corpus.

A v2 corpus file is ``{"meta": {...}, "films": [...]}``. Each film carries
``domestic_lifetime``/``worldwide_lifetime`` (int or None) plus a matching
``*_status`` of ``reported``, ``missing`` or ``confirmed_zero``, and an
``identity_status`` of ``verified`` or ``unresolved``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

SCHEMA_VERSION = 2
METRICS = ("domestic", "worldwide")
METRIC_STATUSES = {"reported", "missing", "confirmed_zero"}
IDENTITY_STATUSES = {"verified", "unresolved"}
MIN_ROWS = 1800
MAX_SHRINK = 0.05
EXAMPLES_PER_CODE = 5


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str


def coverage(films: list[dict]) -> dict:
    """Share of rows whose gross is reported, per metric."""
    total = len(films)
    out = {}
    for m in METRICS:
        counts = Counter(f.get(f"{m}_status") for f in films)
        out[m] = {
            "reported": counts.get("reported", 0),
            "missing": counts.get("missing", 0),
            "confirmed_zero": counts.get("confirmed_zero", 0),
            "reported_share": round(counts.get("reported", 0) / total, 4) if total else 0.0,
        }
    return out


def _metric_problem(film: dict, metric: str) -> str | None:
    status = film.get(f"{metric}_status")
    value = film.get(f"{metric}_lifetime")
    if status not in METRIC_STATUSES:
        return f"unknown {metric}_status {status!r}"
    if status == "reported" and not (isinstance(value, int) and value > 0):
        return f"{metric} reported but value is {value!r}"
    if status == "missing" and value is not None:
        return f"{metric} missing but value is {value!r}"
    if status == "confirmed_zero" and value != 0:
        return f"{metric} confirmed_zero but value is {value!r}"
    return None


def row_problems(film: dict) -> list[str]:
    """Row-level defects that make a single record unusable (not file-level gates)."""
    problems = []
    if not film.get("imdb_id"):
        problems.append("missing imdb_id")
    ident = film.get("identity_status")
    if ident not in IDENTITY_STATUSES:
        problems.append(f"identity_status {ident!r}")
    elif ident == "verified" and not isinstance(film.get("tmdb_id"), int):
        problems.append("verified without tmdb_id")
    metric = [p for p in (_metric_problem(film, m) for m in METRICS) if p]
    problems += metric
    if not metric and film.get("domestic_status") == "reported" and film.get("worldwide_status") == "reported":
        if film["worldwide_lifetime"] < film["domestic_lifetime"]:
            problems.append("worldwide below domestic")
    return problems


def validate_corpus(corpus: dict, previous: dict | None = None, min_rows: int = MIN_ROWS) -> list[Violation]:
    """Return every gate violation; an empty list means the corpus may be written/used."""
    if not isinstance(corpus, dict) or "films" not in corpus or "meta" not in corpus:
        return [Violation("bad_format", "expected {'meta': ..., 'films': [...]}")]

    meta, films = corpus["meta"], corpus["films"]
    found: dict[str, list[str]] = {}

    def add(code: str, detail: str) -> None:
        found.setdefault(code, []).append(detail)

    if meta.get("corpus_schema_version") != SCHEMA_VERSION:
        add("schema_version", f"expected {SCHEMA_VERSION}, got {meta.get('corpus_schema_version')!r}")
    if len(films) < min_rows:
        add("too_few_rows", f"{len(films)} < {min_rows}")
    if previous is not None:
        prev_n = len(previous.get("films", previous) if isinstance(previous, dict) else previous)
        if prev_n and len(films) < prev_n * (1 - MAX_SHRINK):
            add("shrank", f"{len(films)} rows vs {prev_n} previously")

    for imdb_id, n in Counter(f.get("imdb_id") for f in films).items():
        if not imdb_id:
            add("missing_imdb_id", f"{n} row(s)")
        elif n > 1:
            add("duplicate_imdb_id", f"{imdb_id} x{n}")

    verified = [f for f in films if f.get("identity_status") == "verified"]
    for tmdb_id, n in Counter(f.get("tmdb_id") for f in verified).items():
        if n > 1:
            add("duplicate_tmdb_id", f"{tmdb_id} x{n}")

    for f in films:
        ident = f.get("identity_status")
        if ident not in IDENTITY_STATUSES:
            add("identity_status", f"{f.get('imdb_id')}: {ident!r}")
        elif ident == "verified" and not isinstance(f.get("tmdb_id"), int):
            add("identity_status", f"{f.get('imdb_id')}: verified without tmdb_id")
        problems = [p for p in (_metric_problem(f, m) for m in METRICS) if p]
        for problem in problems:
            add("metric_status", f"{f.get('imdb_id')}: {problem}")
        # Only compare grosses whose status and value are consistent.
        if not problems and f.get("domestic_status") == "reported" and f.get("worldwide_status") == "reported":
            if f["worldwide_lifetime"] < f["domestic_lifetime"]:
                add("ww_below_dom", f"{f.get('imdb_id')}: ww {f['worldwide_lifetime']} < dom {f['domestic_lifetime']}")

    violations = []
    for code, details in found.items():
        shown = details[:EXAMPLES_PER_CODE]
        more = f" (+{len(details) - len(shown)} more)" if len(details) > len(shown) else ""
        violations.append(Violation(code, "; ".join(shown) + more))
    return violations
