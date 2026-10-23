"""Historical box-office headline analysis on a sanitized Cerebro snapshot (read-only).

    python scripts/headline_analysis.py --snapshot <sanitized cerebro_MW.csv> [--as-of YYYY-MM-DD]
                                        [--power-words <power_words.json>] [--out out/headline_analysis]

Writes summary.json, summary.md and audit_sample.csv (the 10% sample for an editor to mark
editor_construction_ok / editor_strategy_ok) under --out. Writes nothing else and contacts no
service. Output describes MovieWeb's past headlines (historical_not_faramir); keep it out of git.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir.analytics import cerebro_snapshot, power_words  # noqa: E402
from faramir.headlines.analysis import analyze  # noqa: E402
from faramir.headlines.recommend import recommend  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="headline_analysis.py")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--power-words", default=None)
    ap.add_argument("--out", default=os.path.join("out", "headline_analysis"))
    args = ap.parse_args(argv)
    snap = cerebro_snapshot.load(args.snapshot, date.fromisoformat(args.as_of) if args.as_of else None)
    a = analyze(snap)
    words = power_words.load(args.power_words) if args.power_words else []
    os.makedirs(args.out, exist_ok=True)
    recs = {s: recommend(s, a, words) for s in ("overtake", "chase", "countdown", "takeover", "hold")}
    summary = {"snapshot_as_of": a.as_of, "as_of_basis": snap.as_of_basis, "team": a.team,
               "box_office_articles": a.articles, "baseline": a.baseline, "label": a.label,
               "constructions": {k: v.as_dict() for k, v in sorted(a.constructions.items(), key=lambda kv: -kv[1].n)},
               "recommendations": {k: {"constructions": [c["construction"] for c in r.constructions],
                                       "rationale_codes": r.rationale_codes} for k, r in recs.items()},
               "audit_sample_size": len(a.audit_sample)}
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(args.out, "audit_sample.csv"), "w", newline="") as f:
        if a.audit_sample:
            w = csv.DictWriter(f, fieldnames=list(a.audit_sample[0]))
            w.writeheader()
            w.writerows({**r, "constructions": "|".join(r["constructions"])} for r in a.audit_sample)
    lines = [f"# Box-office headline constructions (MW News pool, as of {a.as_of})", "",
             f"{a.articles} matured box-office articles; baseline median {a.baseline['median']} cumulative sessions/article "
             f"(ages {a.baseline['age_days_min']}–{a.baseline['age_days_max']} days). Historical, not Faramir-originated.", "",
             "| Construction | n | Median | Lift vs baseline | Median age (days) | Failure rate | Heavy hitters | |",
             "|---|---|---|---|---|---|---|---|"]
    for k, v in sorted(a.constructions.items(), key=lambda kv: -kv[1].n):
        lines.append(f"| {k} | {v.n} | {v.median} | {v.lift_vs_baseline} | {v.age_days_median} | {v.failure_rate} | {v.heavy_hitter_rate} | "
                     f"{'experimental' if v.experimental else ''} |")
    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({k: summary[k] for k in ("snapshot_as_of", "box_office_articles", "audit_sample_size")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
