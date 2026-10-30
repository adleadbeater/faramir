"""scripts/sync_power_words.py: reads the sheet's CSV download, skips the lookup row, refuses a changed layout."""

import csv
import importlib.util
import os

import pytest

spec = importlib.util.spec_from_file_location("sync_power_words", os.path.join(os.path.dirname(__file__), "..", "scripts", "sync_power_words.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

from faramir.titles import data  # noqa: E402


def sheet_csv(path, n=120, header=("Use", "Sessions", "S/A", "FR", "HH")):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Only Write In Cell A3. Don't Edit Anything Below", "MovieWeb", "", "", "", "", "", "Screen"])
        w.writerow([""] + list(header) + ["", "Use", "Sessions", "S/A", "FR", "HH", "Note"])
        w.writerow(["lookup phrase", "9", "9", "9", "0%", "0%"])
        w.writerow(["Box Office", "129", "1,417,297", "10,987", "9%", "12%", "", "561", "4,265,387", "7,603", "21%", "7%", "note"])
        w.writerow(["Never Used", "0", "0", "", "", ""])
        for i in range(n):
            w.writerow([f"Phrase {i}", "5", "50,000", "10,000", "3%", "20%"])


def test_sync_reads_movieweb_columns_and_skips_lookup_row(tmp_path):
    sheet_csv(tmp_path / "pw.csv")
    dest = tmp_path / "pw.tsv"
    s = sync.sanitize(str(tmp_path / "pw.csv"), str(dest))
    phrases, _ = data.load_power_words(None, str(dest))
    names = [p.phrase for p in phrases]
    assert "lookup phrase" not in names and s["phrases"] == 122
    box = next(p for p in phrases if p.phrase == "Box Office")
    assert (box.sheet_uses, box.sheet_sa) == (129, 10987)


def test_sync_refuses_changed_layout_or_short_sheet(tmp_path):
    dest = tmp_path / "pw.tsv"
    sheet_csv(tmp_path / "bad.csv", header=("Use", "Sessions", "Avg", "FR", "HH"))
    with pytest.raises(SystemExit):
        sync.sanitize(str(tmp_path / "bad.csv"), str(dest))
    sheet_csv(tmp_path / "short.csv", n=10)
    with pytest.raises(SystemExit):
        sync.sanitize(str(tmp_path / "short.csv"), str(dest))
    assert not dest.exists()
