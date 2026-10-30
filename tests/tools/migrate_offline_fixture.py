"""Re-generate fixtures/offline/<day>/sheets.json as scripts/migrate_sheets.py leaves it.

The offline day models a production workbook AFTER the approved migration (preflight refuses
anything else). Runs the real migration against an in-memory copy; touches no real Sheets.

    python tests/tools/migrate_offline_fixture.py fixtures/offline/2026-10-22/sheets.json
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from faramir import runtime  # noqa: E402
from tests.fakes.fake_gspread import FakeClient  # noqa: E402


def main(path: str) -> None:
    spec = importlib.util.spec_from_file_location("migrate_sheets", os.path.join(ROOT, "scripts", "migrate_sheets.py"))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with open(path, encoding="utf-8") as f:
        books = json.load(f)
    out = {}
    for key, tabs in books.items():
        gc = FakeClient()
        for name, values in tabs.items():
            gc.book(key).add_tab(name, values)
        ctx = runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id="fixture-migration", out_dir="/tmp")
        with runtime.use(ctx):  # writes reach only the in-memory copy
            print(key, mig.apply(gc, key, "fixture-migration"))
        out[key] = {name: ws.data for name, ws in gc.book(key)._sheets.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1])
