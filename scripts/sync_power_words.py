"""Load a CSV download of the Power Words Sheet into Faramir's local store.

    bash scripts/sync-power-words.sh [path/to/csv]

The sheet (18CGvf8jl1i_vXcXDdinXnHNFSs60URRVSftaZ89eUi0, tab 'Data Sheet ^^^') is a native Google Sheet,
so Google Drive for desktop shows only a shortcut to it. Download it from the sheet (File → Download →
Comma-separated values) and run this; with no path it takes the newest matching CSV in ~/Downloads.
Only the MovieWeb columns (B–F) are kept. Row 3 is the sheet's "try a phrase" lookup cell and is skipped.
The result is checked with Faramir's own loader before it replaces out/local_store/power_words_sheet.tsv.
"""

from __future__ import annotations

import csv
import glob
import os
import sys
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from faramir.titles import data  # noqa: E402

DEST = os.path.join(ROOT, "out", "local_store", "power_words_sheet.tsv")
DOWNLOAD_GLOB = os.path.expanduser("~/Downloads/*Power Words*Data Sheet*.csv")
HEADER = ["Use", "Sessions", "S/A", "FR", "HH"]   # MovieWeb block, columns B–F
FIRST_DATA_ROW = 4                                # rows 1–2 are headers, row 3 is the lookup cell
MIN_PHRASES = 100


def newest_download() -> str | None:
    found = glob.glob(DOWNLOAD_GLOB)
    return max(found, key=os.path.getmtime) if found else None


def sanitize(src: str, dest: str) -> dict:
    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2 or [c.strip() for c in rows[1][1:6]] != HEADER:
        raise SystemExit(f"The CSV's layout changed (expected MovieWeb columns {HEADER} in B–F). Nothing was replaced.")
    out = []
    for r in rows[FIRST_DATA_ROW - 1:]:
        if not r or not r[0].strip():
            continue
        cells = [data._num(c) for c in (r + [""] * 6)[1:6]]
        out.append([r[0].strip()] + [str(int(c)) for c in cells])
    tmp = dest + ".tmp"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"# Snapshot of the Power Words Sheet ({data.POWER_WORDS_SHEET_ID}, tab '{data.POWER_WORDS_TAB}', "
                f"rows {FIRST_DATA_ROW}+, MovieWeb columns B-F), from {os.path.basename(src)}, loaded {date.today()}\n")
        f.write("phrase\tuse\tsessions\tsa\tfr\thh\n")
        for row in out:
            f.write("\t".join(row) + "\n")
    phrases, _ = data.load_power_words(None, tmp)
    if len(phrases) < MIN_PHRASES:
        os.remove(tmp)
        raise SystemExit(f"Only {len(phrases)} phrases in the CSV (expected {MIN_PHRASES}+). Nothing was replaced.")
    os.replace(tmp, dest)
    return {"rows": len(out), "phrases": len(phrases)}


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else newest_download()
    if not src or not os.path.exists(src):
        print("No Power Words CSV found. Download the sheet as CSV (File → Download → Comma-separated values)\n"
              f"or pass its path. Looked for: {DOWNLOAD_GLOB}")
        return 1
    s = sanitize(src, DEST)
    print(f"Power Words synced: {s['phrases']} phrases from {os.path.basename(src)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
