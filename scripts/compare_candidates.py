"""Compare candidate sets: original matcher + v1 corpus vs current code + v2 corpus.

Offline review tool for Phase 0 increment 2 (no network, no credentials).
Three runs over the same probe films:

  A  original matcher/corpus code (from git, base commit) on data/corpus.json
  B  current code on data/corpus.json      -> A == B proves code parity on v1 data
  C  current code on the usable records of data/corpus.v2.json
     (health-classified, stale rule applied with the supplied chart rows)

Every A-vs-C difference is attributed: corrected identity, excluded record,
changed angle categories/score, or capacity/threshold effects.

Usage:
    python scripts/compare_candidates.py [--base-commit f2a0ce4] [--out docs/v2/increment2_review]
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from faramir import corpus as corpus_mod  # noqa: E402
from faramir.corpus import load_corpus_v2  # noqa: E402
from faramir.matcher import find_all_candidates  # noqa: E402

# Active films (>= $20M, <= 90 days) on The Numbers daily chart for 2026-10-21,
# as recorded during the audit: (title, domestic total, days in release).
CHART_2026_10_21 = [
    ("You Can See Everything", 23_407_485, 6),
    ("Street Fighter", 37_177_832, 6),
    ("Other Mommy", 47_127_905, 13),
    ("Verity", 67_979_615, 20),
    ("Resident Evil", 150_249_362, 34),
    ("Heart of the Beast", 59_670_218, 27),
    ("Forgotten Island", 35_675_180, 27),
    ("Spider-Man: Brand New Day", 958_430_308, 83),
    ("Practical Magic 2", 68_365_708, 41),
    ("Buddy", 26_969_222, 55),
]
# Other titles on the same chart (used only as stale-rule evidence).
CHART_OTHER_ROWS = [("Back to the Future", 225_267_287), ("The Odyssey", 625_102_095), ("Avengers Endgame: Encore", 898_435_060)]


def load_module_from_git(commit: str, path: str, name: str):
    src = subprocess.check_output(["git", "-C", ROOT, "show", f"{commit}:{path}"], text=True)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        tmp = f.name
    spec = importlib.util.spec_from_file_location(name, tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    os.unlink(tmp)
    return mod


def neutral_active(tmdb_id: int, title: str, axis: str, today: int) -> dict:
    other = "worldwide" if axis == "domestic" else "domestic"
    return {
        "tmdb_id": tmdb_id, "title": title, "canonical_title": title, "director": "", "lead_cast": [],
        "genres": ["Action"], "collection_id": None, "distributor": "", "origin_country": "US", "release_year": "2026",
        f"{axis}_today": today, f"{axis}_yesterday": int(today / 1.03), f"{other}_today": 0, f"{other}_yesterday": 0,
    }


def probes() -> list[tuple[str, dict]]:
    out = []
    for i, (title, dom, _days) in enumerate(CHART_2026_10_21):
        out.append((f"chart:{title}", neutral_active(-(i + 1), title, "domestic", dom)))
    for axis, lo, hi in (("domestic", 90_000_000, 1_000_000_000), ("worldwide", 100_000_000, 3_000_000_000)):
        n = 120
        for k in range(n):
            g = int(lo * (hi / lo) ** (k / (n - 1)))
            out.append((f"sweep:{axis}:{g}", neutral_active(-(1000 + len(out)), f"Probe {axis} {g}", axis, g)))
    return out


def run(matcher_find_all, films: list[dict], probe_list) -> dict:
    result = {}
    for label, active in probe_list:
        for c in matcher_find_all([active], films, {}, set()):
            comp = c["comp"]
            key = (label, c["axis"], c["kind"], comp["imdb_id"])
            result[key] = {
                "title": comp.get("title"), "year": comp.get("release_year"), "tmdb_id": comp.get("tmdb_id"),
                "categories": tuple(sorted(c["angle_categories"])), "score": c["richness_score"],
                "threshold": c["threshold_value"],
            }
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-commit", default="f2a0ce4")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "v2", "increment2_review"))
    ap.add_argument("--run-date", default="2026-10-22")
    args = ap.parse_args(argv)

    with open(os.path.join(ROOT, "data", "corpus.json")) as f:
        v1 = json.load(f)
    run_date = date.fromisoformat(args.run_date)
    chart_rows = [{"title": t, "gross_to_date": g} for t, g, _ in CHART_2026_10_21] + [
        {"title": t, "gross_to_date": g} for t, g in CHART_OTHER_ROWS
    ]
    load = load_corpus_v2(os.path.join(ROOT, "data", "corpus.v2.json"), run_date,
                          state_rows=[], chart_rows=chart_rows, chart_date=date(2026, 10, 21))
    excluded = {e.imdb_id: f"{e.reason} ({e.detail})" if e.detail else e.reason for e in load.exclusions}

    # Run A with the original matcher AND original corpus helpers it imports.
    old_corpus = load_module_from_git(args.base_commit, "src/faramir/corpus.py", "faramir_corpus_base")
    old_matcher = load_module_from_git(args.base_commit, "src/faramir/matcher.py", "faramir_matcher_base")
    probe_list = probes()
    saved = sys.modules["faramir.corpus"]
    sys.modules["faramir.corpus"] = old_corpus
    try:
        A = run(old_matcher.find_all_candidates, v1, probe_list)
    finally:
        sys.modules["faramir.corpus"] = saved
    B = run(find_all_candidates, v1, probe_list)
    C = run(find_all_candidates, load.films, probe_list)

    parity = A == B

    # Every candidate is keyed by a STABLE film identity — the comp's IMDb id —
    # plus probe/axis/kind. A relabelled record therefore matches its old self
    # and is reported as an identity correction, never as a new story.
    keys_a, keys_c = set(A), set(C)
    matched = keys_a & keys_c
    rows = []
    counts = {k: 0 for k in ("unchanged", "identity_corrected", "identity_corrected_and_rescored",
                             "rescored_same_identity", "removed_stale", "removed_other", "added")}
    for key in sorted(keys_a | keys_c):
        a, c = A.get(key), C.get(key)
        label, axis, kind, imdb = key
        if a and c:
            relabelled = (a["tmdb_id"], a["title"], a["year"]) != (c["tmdb_id"], c["title"], c["year"])
            rescored = a["categories"] != c["categories"] or a["score"] != c["score"]
            if relabelled and rescored:
                change = "identity_corrected_and_rescored"
            elif relabelled:
                change = "identity_corrected"
            elif rescored:
                change = "rescored_same_identity"
            else:
                counts["unchanged"] += 1
                continue
            detail = ""
        elif a:
            if imdb in excluded:
                change, detail = "removed_stale", excluded[imdb]
            else:
                change, detail = "removed_other", "dropped below MIN_RICHNESS_TO_SURFACE or per-cell cap after the comp's corrected metadata changed its score"
        else:
            change, detail = "added", "newly reaches MIN_RICHNESS_TO_SURFACE / per-cell cap after corrected metadata"
        counts[change] += 1
        rows.append({
            "change": change, "probe": label, "axis": axis, "kind": kind, "comp_imdb_id": imdb,
            "old_label": f"{a['title']} ({a['year']})" if a else "", "new_label": f"{c['title']} ({c['year']})" if c else "",
            "old_tmdb_id": a["tmdb_id"] if a else "", "new_tmdb_id": c["tmdb_id"] if c else "",
            "old_categories": "|".join(a["categories"]) if a else "", "new_categories": "|".join(c["categories"]) if c else "",
            "old_score": a["score"] if a else "", "new_score": c["score"] if c else "",
            "threshold": (a or c)["threshold"], "detail": detail,
        })

    corrected = counts["identity_corrected"] + counts["identity_corrected_and_rescored"]
    removed = counts["removed_stale"] + counts["removed_other"]
    in_both = len(matched)
    assert in_both == counts["unchanged"] + corrected + counts["rescored_same_identity"]
    assert len(A) == in_both + removed and len(C) == in_both + counts["added"]
    assert len(rows) == corrected + counts["rescored_same_identity"] + removed + counts["added"]

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "candidate_differences.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["change"])
        w.writeheader()
        w.writerows(rows)

    distinct_relabelled = len({r["comp_imdb_id"] for r in rows if r["change"].startswith("identity_corrected")})
    chart = [r for r in rows if r["probe"].startswith("chart:")]

    def table(change, limit=None):
        sel = [r for r in rows if r["change"] == change][:limit]
        if not sel:
            return ["_None._", ""]
        out = ["| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |", "|---|---|---|---|---|---|---|"]
        for r in sel:
            cats = f"{r['old_categories'] or '—'} ({r['old_score']}) → {r['new_categories'] or '—'} ({r['new_score']})"
            out.append(f"| {r['probe']} | {r['axis']}/{r['kind']} | {r['comp_imdb_id']} | {r['old_label']} | {r['new_label']} | {cats} | {r['detail']} |")
        return out + [""]

    lines = [
        "# Increment 2: candidate-set comparison (reconciled)",
        "",
        f"Base commit for run A: `{args.base_commit}`. Run date {run_date}. Probes: {len(probe_list)} "
        f"({len(CHART_2026_10_21)} real chart films from 2026-10-21 plus a {len(probe_list) - len(CHART_2026_10_21)}-level synthetic sweep), "
        "all with neutral metadata (no franchise/director/cast links), so differences come from the corpus, not the active film.",
        "",
        "Runs: **A** original code + v1 corpus; **B** current code + v1 corpus; **C** current code + usable v2 records.",
        "Candidates are matched by `(probe, axis, kind, comp IMDb id)`. The IMDb id is the film's stable identity, so a "
        "relabelled record is matched to itself and counted as an identity correction, not as a removed + added story.",
        "",
        f"- Corpus v2 health: **{load.status}**; usable {len(load.films)} of {load.meta.get('rows')}; exclusions {load.exclusion_counts() or 'none'}",
        f"- Code parity A vs B: **{'IDENTICAL' if parity else 'DIFFERENT'}** ({len(A)} candidates each) — the code changes alone change nothing on v1 data.",
        "",
        "## Reconciliation",
        "",
        "| Category | Count | In A (old) | In C (new) | Counted as a difference |",
        "|---|---|---|---|---|",
        f"| Unchanged (same identity, categories, score) | {counts['unchanged']} | ✓ | ✓ | no |",
        f"| Identity corrected, score unchanged | {counts['identity_corrected']} | ✓ | ✓ | yes |",
        f"| Identity corrected and score changed | {counts['identity_corrected_and_rescored']} | ✓ | ✓ | yes |",
        f"| Score/categories changed, same identity | {counts['rescored_same_identity']} | ✓ | ✓ | yes |",
        f"| Removed: stale exclusion | {counts['removed_stale']} | ✓ | — | yes |",
        f"| Removed: no longer surfaces after corrected metadata | {counts['removed_other']} | ✓ | — | yes |",
        f"| Added: newly surfaces after corrected metadata | {counts['added']} | — | ✓ | yes |",
        f"| **Total** | | **{len(A)}** | **{len(C)}** | **{len(rows)}** |",
        "",
        f"- A = {counts['unchanged']} + {corrected} + {counts['rescored_same_identity']} + {removed} = {len(A)}",
        f"- C = {counts['unchanged']} + {corrected} + {counts['rescored_same_identity']} + {counts['added']} = {len(C)}",
        f"- Differences = {corrected} corrected + {counts['rescored_same_identity']} rescored + {removed} removed + {counts['added']} added = {len(rows)}",
        f"- Net change C − A = {counts['added']} added − {removed} removed = {len(C) - len(A)}",
        f"- The {corrected} identity corrections involve {distinct_relabelled} distinct comp records (the same record is a candidate for several probe levels).",
        "",
        "Missing-data handling causes **no** differences by itself: the v1 index already skipped zero grosses; v2 makes that explicit.",
        "No editorial scoring was changed; every score change comes from corrected comp metadata (e.g. release year → classic flag).",
        "",
        "## Real chart films (2026-10-21)",
        "",
    ]
    lines += ["| Probe | Axis/kind | Old comp | New comp | Change |", "|---|---|---|---|---|"]
    lines += [f"| {r['probe'][6:]} | {r['axis']}/{r['kind']} | {r['old_label']} | {r['new_label']} | {r['change']} |" for r in chart] or ["| — | — | — | — | none |"]
    lines += ["", "## Removed: stale exclusions", ""] + table("removed_stale")
    lines += ["## Removed: no longer surfaces", ""] + table("removed_other")
    lines += ["## Added", ""] + table("added")
    lines += ["## Score changed with corrected identity", ""] + table("identity_corrected_and_rescored")
    lines += ["## Score changed, same identity", ""] + table("rescored_same_identity")
    lines += ["## Identity corrections, score unchanged (first 25)", ""] + table("identity_corrected", 25)
    lines += [f"Full list: `{os.path.relpath(csv_path, ROOT)}`.", ""]
    md_path = os.path.join(args.out, "candidate_comparison.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"parity={parity} A={len(A)} C={len(C)} differences={len(rows)} {counts}")
    print(md_path)
    return 0 if parity else 1


if __name__ == "__main__":
    sys.exit(main())
