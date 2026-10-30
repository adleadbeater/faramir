"""A local, file-backed stand-in for Faramir's Google Sheet, for development runs only.

Faramir's sheet code talks to a gspread-like client. Until deployment, real-data runs keep their
state and weekend history in ``out/local_store/workbook.json`` instead of the production Sheet.
The in-memory worksheet behaviour comes from the test suite's FakeClient; this module adds
loading and saving. Nothing here can reach Google.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (os.path.join(ROOT, "src"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from tests.fakes.fake_gspread import FakeClient  # noqa: E402

STORE_DIR = os.path.join(ROOT, "out", "local_store")
WORKBOOK = os.path.join(STORE_DIR, "workbook.json")
SHEET_ID = "local"


def load(path: str = WORKBOOK) -> FakeClient:
    gc = FakeClient()
    if os.path.exists(path):
        for book_id, tabs in json.load(open(path)).items():
            book = gc.book(book_id)
            for title, rows in tabs.items():
                book.add_tab(title, rows)
    return gc


def save(gc: FakeClient, path: str = WORKBOOK) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {bid: {t: ws.rows for t, ws in book._sheets.items()} for bid, book in gc._books.items()}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_env(names=("TMDB_API_KEY",), path: str = os.path.expanduser("~/.claude/.env.json")) -> list[str]:
    """Set the named variables from the local credentials file (KEY=VALUE lines) if not already set.
    Returns the names that were set. Values are never printed."""
    found = []
    if not os.path.exists(path):
        return found
    for line in open(path):
        key, sep, value = line.strip().partition("=")
        if sep and key in names and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"')
            found.append(key)
    return found
