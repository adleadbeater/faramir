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
    rows = []
    for key in sorted(set(A) | set(C)):
        a, c = A.get(key), C.get(key)
        label, axis, kind, imdb = key
        if a and c:
            if (a["tmdb_id"], a["title"], a["year"]) != (c["tmdb_id"], c["title"], c["year"]):
                reason = "identity_corrected"
            elif a["categories"] != c["categories"] or a["score"] != c["score"]:
                reason = "categories_or_score_changed"
            else:
                continue
        elif a:
            reason = f"excluded:{excluded[imdb]}" if imdb in excluded else "not_surfaced_after_rescoring_or_cap"
        else:
            reason = "newly_surfaced_after_rescoring_or_cap"
        rows.append({
            "probe": label, "axis": axis, "kind": kind, "imdb_id": imdb, "reason": reason,
            "old_label": f"{a['title']} ({a['year']})" if a else "", "new_label": f"{c['title']} ({c['year']})" if c else "",
            "old_categories": "|".join(a["categories"]) if a else "", "new_categories": "|".join(c["categories"]) if c else "",
            "old_score": a["score"] if a else "", "new_score": c["score"] if c else "",
            "threshold": (a or c)["threshold"],
        })

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "candidate_differences.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["probe"])
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    by_reason = Counter(r["reason"].split(" (")[0] for r in rows)
    chart = [r for r in rows if r["probe"].startswith("chart:")]
    lines = [
        "# Increment 2: candidate-set comparison",
        "",
        f"Base commit for run A: `{args.base_commit}`. Run date {run_date}. Probes: {len(probe_list)} "
        f"({len(CHART_2026_10_21)} real chart films from 2026-10-21 plus a {len(probe_list) - len(CHART_2026_10_21)}-level synthetic sweep). "
        "All probes use neutral metadata (no franchise/director/cast links), so differences come from the corpus, not the active film.",
        "",
        f"- Corpus v2 health: **{load.status}**; usable {len(load.films)} of {load.meta.get('rows')}; exclusions {load.exclusion_counts() or 'none'}",
        f"- Code parity (A original code vs B current code, both on v1 corpus): **{'IDENTICAL' if parity else 'DIFFERENT'}** — {len(A)} candidates each",
        f"- Candidates: A (old) {len(A)}, C (new) {len(C)}; differences {len(rows)}",
        "",
        "## Differences by cause (A → C)",
        "",
        "| Cause | Count |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in by_reason.most_common()]
    lines += [
        "",
        "Missing-data handling causes **no** candidate differences by itself: the v1 index already skipped zero grosses, and v2 makes that explicit (`None`/`missing`).",
        "",
        "## Real chart films (2026-10-21)",
        "",
        "| Probe | Axis/kind | Old comp | New comp | Cause |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {r['probe'][6:]} | {r['axis']}/{r['kind']} | {r['old_label']} | {r['new_label']} | {r['reason']} |" for r in chart]
    if not chart:
        lines.append("| — | — | — | — | no differences |")
    lines += ["", "## Identity corrections seen in the sweep (first 30)", "", "| Probe | Old comp | New comp |", "|---|---|---|"]
    lines += [f"| {r['probe']} | {r['old_label']} | {r['new_label']} |" for r in rows if r["reason"] == "identity_corrected"][:30]
    lines += ["", f"Full list: `{os.path.relpath(csv_path, ROOT)}`.", ""]
    md_path = os.path.join(args.out, "candidate_comparison.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"parity={parity} A={len(A)} C={len(C)} differences={len(rows)} {dict(by_reason)}")
    print(md_path)
    return 0 if parity else 1


if __name__ == "__main__":
    sys.exit(main())
