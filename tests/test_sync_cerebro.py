"""scripts/sync_cerebro.py: keeps only allow-listed columns, converts dates, refuses a changed export."""

import csv
import importlib.util
import os

import pytest

spec = importlib.util.spec_from_file_location("sync_cerebro", os.path.join(os.path.dirname(__file__), "..", "scripts", "sync_cerebro.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def raw(path, n=2100, drop=None):
    cols = list(sync.KEEP_BQ) + ["Writer", "SrEditor", "WriterCost", "Revenue"]
    cols = [c for c in cols if c != drop]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i in range(n):
            row = {c: "" for c in cols}
            row.update({"ArticleTitle": f"Film {i} Hits the Box Office", "URL": f"https://movieweb.com/{i}/",
                        "PubDatetime": "10/27/2026 9:36:25", "PubType": "Publish", "Brand": "MW", "PriTag": "Box Office",
                        "AllTags": "Box Office|Action", "ContentType": "News", "PageType": "Regular", "ActSess": "1200",
                        "Writer": "Someone", "WriterCost": "99"})
            w.writerow({k: v for k, v in row.items() if k in cols})


def test_sync_keeps_only_safe_columns_and_iso_dates(tmp_path):
    raw(tmp_path / "raw.csv")
    s = sync.sanitize(str(tmp_path / "raw.csv"), str(tmp_path / "out.csv"))
    header = open(tmp_path / "out.csv").readline().strip().split(",")
    assert header == list(sync.KEEP_BQ.values()) and "Writer" not in header and "WriterCost" not in header
    assert s["rows"] == 2100 and s["newest"] == "2026-10-27" and s["unreadable_dates"] == 0


def test_sync_refuses_a_changed_or_short_export(tmp_path):
    raw(tmp_path / "raw.csv", drop="ActSess")
    with pytest.raises(SystemExit):
        sync.sanitize(str(tmp_path / "raw.csv"), str(tmp_path / "out.csv"))
    raw(tmp_path / "short.csv", n=10)
    with pytest.raises(SystemExit):
        sync.sanitize(str(tmp_path / "short.csv"), str(tmp_path / "out.csv"))
    assert not os.path.exists(tmp_path / "out.csv")
