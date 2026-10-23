"""TMDB-only identity repair of the comparison corpus (v1 list → v2 file).

The v1 builder resolved each BOM row by a year-less, most-popular TMDB title
search, so ~16% of rows carry another film's identity and metadata while
keeping their own (correct) BOM IMDb id and grosses. Repair re-derives identity
from the IMDb id via TMDB /find and reloads metadata only for rows whose
identity changes. Grosses, BOM rank and Best Picture flags (IMDb-keyed) are
kept. No BOM or The Numbers request is made.

Zero grosses in v1 mean "not on that BOM chart", not "grossed $0", so they
become ``None`` with status ``missing``. Nothing in TMDB-only mode can prove a
film grossed zero, so ``confirmed_zero`` is never produced here.
"""

from __future__ import annotations

import csv
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from faramir.corpus_validate import METRICS, SCHEMA_VERSION, Violation, coverage

IDENTITY_SOURCE = "tmdb_find"
CREATIVE_FIELDS = {
    "director": "",
    "lead_cast": [],
    "genres": [],
    "collection_id": None,
    "collection_name": "",
    "origin_country": "",
    "distributor": "",
}
DETAIL_FIELDS = ("title", "release_date", "release_year", *CREATIVE_FIELDS)

FindFn = Callable[[str], list]
DetailsFn = Callable[[int], dict]


def is_classic(release_year, bom_gross) -> bool:
    """Same rule as scripts/build_corpus.enrich_with_tmdb, applied to the true year."""
    try:
        year = int(release_year or 0)
    except (TypeError, ValueError):
        year = 0
    gross = int(bom_gross or 0)
    return year > 0 and (year < 1980 or (year < 2000 and gross > 500_000_000))


def _with_metric_status(row: dict) -> dict:
    for m in METRICS:
        value = row.get(f"{m}_lifetime")
        if value:
            row[f"{m}_lifetime"] = int(value)
            row[f"{m}_status"] = "reported"
        else:
            row[f"{m}_lifetime"] = None
            row[f"{m}_status"] = "missing"
    return row


def repair_films(
    films: list[dict],
    find_fn: FindFn,
    details_fn: DetailsFn,
    checked_at: str,
    workers: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Return (repaired films, change records). Any lookup exception propagates."""
    imdb_ids = [f["imdb_id"] for f in films]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        finds = dict(zip(imdb_ids, pool.map(find_fn, imdb_ids)))

    repaired, changes = [], []
    for old in films:
        row = dict(old)
        results = finds[old["imdb_id"]] or []
        row["identity_source"] = IDENTITY_SOURCE
        row["identity_checked_at"] = checked_at

        reason = None
        if len(results) != 1:
            reason = f"find returned {len(results)} movie results"
        else:
            true_id = int(results[0]["id"])
            if true_id == old.get("tmdb_id"):
                row["identity_status"] = "verified"
            else:
                details = details_fn(true_id)
                if details.get("imdb_id") and details["imdb_id"] != old["imdb_id"]:
                    reason = f"details imdb_id {details['imdb_id']} != {old['imdb_id']}"
                else:
                    for k in DETAIL_FIELDS:
                        if k in details:
                            row[k] = details[k]
                    row["tmdb_id"] = true_id
                    row["is_classic"] = is_classic(row.get("release_year"), old.get("bom_gross"))
                    row["identity_status"] = "verified"
                    changes.append({
                        "change": "relabelled",
                        "imdb_id": old["imdb_id"],
                        "old_tmdb_id": old.get("tmdb_id"),
                        "old_title": old.get("title"),
                        "old_year": old.get("release_year"),
                        "new_tmdb_id": true_id,
                        "new_title": row.get("title"),
                        "new_year": row.get("release_year"),
                        "reason": "",
                    })

        if reason:
            # Never substitute a guess: keep grosses for numeric use, drop the
            # (possibly wrong) TMDB id and every creative field.
            row.update(CREATIVE_FIELDS)
            row["tmdb_id"] = None
            row["identity_status"] = "unresolved"
            changes.append({
                "change": "unresolved",
                "imdb_id": old["imdb_id"],
                "old_tmdb_id": old.get("tmdb_id"),
                "old_title": old.get("title"),
                "old_year": old.get("release_year"),
                "new_tmdb_id": "",
                "new_title": "",
                "new_year": "",
                "reason": reason,
            })

        repaired.append(_with_metric_status(row))
    return repaired, changes


def build_v2(films: list[dict], gross_as_of: str, repaired_at: str, source_file: str) -> dict:
    return {
        "meta": {
            "corpus_schema_version": SCHEMA_VERSION,
            "gross_as_of": gross_as_of,
            "identity_repaired_at": repaired_at,
            "identity_source": IDENTITY_SOURCE,
            "source_file": source_file,
            "rows": len(films),
            "unresolved": sum(1 for f in films if f["identity_status"] == "unresolved"),
            "coverage": coverage(films),
        },
        "films": films,
    }


CHANGE_COLUMNS = ["change", "imdb_id", "old_tmdb_id", "old_title", "old_year", "new_tmdb_id", "new_title", "new_year", "reason"]


def write_report(out_dir: str, corpus: dict, changes: list[dict], violations: list[Violation], wrote_to: str | None) -> tuple[str, str]:
    """Write report.md and changes.csv; returns their paths."""
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "changes.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CHANGE_COLUMNS)
        w.writeheader()
        w.writerows(changes)

    meta = corpus["meta"]
    cov = meta["coverage"]
    relabelled = [c for c in changes if c["change"] == "relabelled"]
    unresolved = [c for c in changes if c["change"] == "unresolved"]
    lines = [
        "# Corpus identity repair report",
        "",
        f"- Gross figures as of: {meta['gross_as_of']} (unchanged by repair)",
        f"- Repaired at: {meta['identity_repaired_at']} via {meta['identity_source']}",
        f"- Source file: {meta['source_file']}",
        f"- Rows: {meta['rows']}",
        f"- Relabelled identities: {len(relabelled)}",
        f"- Unresolved identities: {len(unresolved)}",
        "",
        "## Metric coverage",
        "",
        "| Metric | Reported | Missing | Confirmed zero | Reported share |",
        "|---|---|---|---|---|",
    ]
    for m, c in cov.items():
        lines.append(f"| {m} | {c['reported']} | {c['missing']} | {c['confirmed_zero']} | {c['reported_share']:.1%} |")
    lines += ["", "## Integrity gates", ""]
    if violations:
        lines += [f"- **FAIL** `{v.code}`: {v.detail}" for v in violations]
    else:
        lines.append("All gates passed.")
    lines += ["", f"Corpus written to: `{wrote_to}`" if wrote_to else "Corpus NOT written.", ""]
    if unresolved:
        lines += ["## Unresolved", "", "| IMDb | Old label | Reason |", "|---|---|---|"]
        lines += [f"| {c['imdb_id']} | {c['old_title']} ({c['old_year']}) | {c['reason']} |" for c in unresolved]
        lines.append("")
    lines += [
        "## Relabelled (first 40 by IMDb id; full list in changes.csv)",
        "",
        "| IMDb | Old label · tmdb | New identity · tmdb |",
        "|---|---|---|",
    ]
    for c in sorted(relabelled, key=lambda c: c["imdb_id"])[:40]:
        lines.append(f"| {c['imdb_id']} | {c['old_title']} ({c['old_year']}) · {c['old_tmdb_id']} | {c['new_title']} ({c['new_year']}) · {c['new_tmdb_id']} |")
    md_path = os.path.join(out_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return md_path, csv_path
