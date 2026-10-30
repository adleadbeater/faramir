"""Copy Alex's daily Cerebro export into Faramir's local store, sanitized.

    bash scripts/sync-cerebro.sh          (from Terminal; see that file for why)

The raw export (Alex's "Cerebro Data/cerebro_MW.csv", rebuilt from BigQuery around 3:45am ET) reaches
only Rob's Google account through Google Drive for desktop. It carries writer/editor names and cost
columns, so only the allow-listed editorial and traffic columns below are kept, renamed to Faramir's
snapshot format, with dates converted to ISO. The result is checked with Faramir's own loader (which
refuses sensitive columns) before it replaces out/local_store/cerebro_MW.csv. Same route as Aragorn's
and arwen's scripts/sync-cerebro.sh; nothing leaves this machine.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from faramir.analytics import cerebro_snapshot  # noqa: E402

DEFAULT_SRC = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-robert.s@valnetinc.com/.shortcut-targets-by-id/"
    "1MIV7occJdVZ_CO0wQu0MByv6-TDgUYO1/Cerebro Data/cerebro_MW.csv")
DEST = os.path.join(ROOT, "out", "local_store", "cerebro_MW.csv")
# BigQuery export column → Faramir snapshot column. The ONLY columns that are kept.
KEEP_BQ = {"ArticleTitle": "title", "URL": "url", "PubDatetime": "pub_date", "PubType": "pub_type", "Brand": "brand",
           "segmentName": "segment", "NetCat": "net_cat", "PriCat": "pri_cat", "PriTag": "pri_tag", "AllTags": "tags",
           "ContentType": "content_type", "PageType": "page_type", "Intent": "intent", "ActSess": "sessions",
           "ActTargetSess": "target_sessions", "ActPageviews": "pageviews", "ActDiscoverClicks": "discover_clicks",
           "ActDiscoverImpressions": "discover_impressions", "ActSeachClicks": "search_clicks"}
MIN_ROWS = 2000


def iso(value: str) -> str:
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip()[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def sanitize(src: str, dest: str) -> dict:
    with open(src, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in KEEP_BQ if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"The export's columns changed; missing {missing}. Nothing was replaced.")
        rows = [{KEEP_BQ[k]: (iso(r[k]) if k == "PubDatetime" else r[k]) for k in KEEP_BQ} for r in reader]
    if len(rows) < MIN_ROWS:
        raise SystemExit(f"Only {len(rows)} rows in the export (expected {MIN_ROWS}+). Nothing was replaced.")
    tmp = dest + ".tmp"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(KEEP_BQ.values()))
        w.writeheader()
        w.writerows(rows)
    snap = cerebro_snapshot.load(tmp)          # refuses sensitive columns; proves Faramir can read it
    os.replace(tmp, dest)
    dates = sorted(r["pub_date"] for r in rows if r["pub_date"])
    return {"rows": len(rows), "newest": dates[-1][:10] if dates else None, "oldest": dates[0][:10] if dates else None,
            "as_of": snap.as_of.isoformat(), "unreadable_dates": sum(1 for r in rows if not r["pub_date"])}


def main() -> int:
    src = os.environ.get("CEREBRO_MW_SRC", DEFAULT_SRC)
    if not os.path.exists(src):
        print(f"Can't see the Cerebro export at:\n  {src}\nIs Google Drive for desktop running and signed in?")
        return 1
    stamp = datetime.fromtimestamp(os.path.getmtime(src)).strftime("%Y-%m-%d %H:%M")
    s = sanitize(src, DEST)
    print(f"Cerebro synced: {s['rows']:,} MovieWeb articles, published {s['oldest']} to {s['newest']} "
          f"(export written {stamp}). Faramir reads it as of {s['as_of']}.")
    if s["unreadable_dates"]:
        print(f"  note: {s['unreadable_dates']} rows had no readable publish date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
