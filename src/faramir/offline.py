"""Fully offline execution from a fixture directory (dry-run only).

``activate(fixtures_dir)`` replaces every external dependency for the duration
of a run:

* real sockets are refused (``OfflineNetworkRefused``);
* ``requests.get``/``requests.post`` are served from ``manifest.json`` routes
  (an unrouted request raises ``OfflineRequestRefused``);
* ``anthropic.Anthropic`` returns a deterministic responder that picks the
  first candidate in the payload (no API call, no credentials);
* Sheets reads come from ``sheets.json``; any Sheets write raises.

Missing settings are filled from the manifest's ``env`` block (placeholder
values only — never credentials) and restored afterwards.

manifest.json::

    {"run_date": "2026-10-22",
     "env": {"FARAMIR_SHEET_ID": "offline", ...},
     "sheets": "sheets.json",
     "http": [{"method": "GET", "prefix": "https://...", "status": 200,
               "file": "tn_daily.html"} | {..., "json": {...}}]}
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from datetime import date

import anthropic
import requests

_STATE: dict = {}


class OfflineNetworkRefused(RuntimeError):
    pass


class OfflineRequestRefused(RuntimeError):
    pass


class OfflineWriteRefused(RuntimeError):
    pass


class _Response:
    def __init__(self, status: int, text: str, url: str):
        self.status_code = status
        self.text = text
        self.url = url

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} for {self.url}")
            err.response = self
            raise err


class _HTTP:
    def __init__(self, base: str, routes: list[dict]):
        self.base = base
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def _serve(self, method: str, url: str) -> _Response:
        self.calls.append((method, url))
        for r in self.routes:
            if r.get("method", "GET").upper() == method and url.startswith(r["prefix"]):
                if "file" in r:
                    with open(os.path.join(self.base, r["file"]), encoding="utf-8") as f:
                        text = f.read()
                else:
                    text = json.dumps(r.get("json", {}))
                return _Response(int(r.get("status", 200)), text, url)
        raise OfflineRequestRefused(f"offline mode: no fixture for {method} {url.split('?')[0]}")

    def get(self, url, params=None, **kwargs):
        return self._serve("GET", url)

    def post(self, url, json=None, **kwargs):
        return self._serve("POST", url)


# ── Sheets (read-only) ───────────────────────────────────────────────────

class _Worksheet:
    def __init__(self, title: str, rows: list[list]):
        self.title = title
        self._rows = [[str(v) for v in r] for r in rows]

    def get_all_records(self, **kwargs):
        if not self._rows:
            return []
        header = self._rows[0]
        out = []
        for r in self._rows[1:]:
            r = r + [""] * (len(header) - len(r))
            rec = {}
            for h, v in zip(header, r):
                rec[h] = int(v) if v.lstrip("-").isdigit() else v
            out.append(rec)
        return out

    def get_all_values(self):
        return [list(r) for r in self._rows]

    def row_values(self, row: int):
        return list(self._rows[row - 1]) if row - 1 < len(self._rows) else []

    def __getattr__(self, name):
        if name in {"update", "append_row", "append_rows", "batch_update", "batch_clear", "clear"}:
            def refuse(*a, **k):
                raise OfflineWriteRefused(f"offline mode: Sheets write {name} on {self.title}")
            return refuse
        raise AttributeError(name)


class _Spreadsheet:
    def __init__(self, tabs: dict[str, list[list]]):
        self._tabs = {k: _Worksheet(k, v) for k, v in tabs.items()}

    def worksheet(self, title: str):
        import gspread

        if title not in self._tabs:
            raise gspread.WorksheetNotFound(title)
        return self._tabs[title]

    def add_worksheet(self, *a, **k):
        raise OfflineWriteRefused("offline mode: add_worksheet")


class _SheetClient:
    def __init__(self, books: dict[str, dict]):
        self._books = {k: _Spreadsheet(v) for k, v in books.items()}

    def open_by_key(self, key: str):
        import gspread

        if key not in self._books:
            raise gspread.SpreadsheetNotFound(key)
        return self._books[key]


def sheet_client() -> _SheetClient:
    if "sheets" not in _STATE:
        raise RuntimeError("offline.sheet_client() called outside offline.activate()")
    return _STATE["sheets"]


def run_date() -> date | None:
    value = _STATE.get("manifest", {}).get("run_date")
    return date.fromisoformat(value) if value else None


# ── Claude ───────────────────────────────────────────────────────────────

class _Usage:
    input_tokens = 0
    output_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Text:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Message:
    stop_reason = "end_turn"
    usage = _Usage()

    def __init__(self, text: str):
        self.content = [_Text(text)]


class _Messages:
    def create(self, **kwargs):
        payload = json.loads(kwargs["messages"][0]["content"])
        picks = []
        for film in payload.get("active_films", []):
            usable = [c for c in film.get("candidates", []) if not c.get("suppressed")]
            if not usable:
                continue
            c = usable[0]
            comp = c.get("comp") or {}
            picks.append({
                "active_tmdb_id": film["tmdb_id"], "active_title": film["title"],
                "comp_tmdb_id": comp.get("tmdb_id"), "comp_title": comp.get("title"),
                "axis": c["axis"], "kind": c["kind"], "threshold_value": c.get("threshold_value"),
                "angle_category": (c.get("angle_categories") or ["studio_sibling"])[0],
                "headline": f"[OFFLINE FIXTURE] {film['title']} vs {comp.get('title') or 'milestone'}",
                "angle": "Deterministic offline responder: first eligible candidate, no model call.",
            })
            break
        return _Message(json.dumps({"picks": picks}))


class _OfflineAnthropic:
    def __init__(self, *args, **kwargs):
        self.messages = _Messages()


# ── activation ───────────────────────────────────────────────────────────

@contextlib.contextmanager
def activate(fixtures_dir: str):
    with open(os.path.join(fixtures_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(fixtures_dir, manifest["sheets"]), encoding="utf-8") as f:
        books = json.load(f)
    http = _HTTP(fixtures_dir, manifest.get("http", []))

    def refuse(*a, **k):
        raise OfflineNetworkRefused("offline mode: real network access refused")

    saved = {
        "connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex,
        "create_connection": socket.create_connection, "get": requests.get, "post": requests.post,
        "anthropic": anthropic.Anthropic,
    }
    saved_env = {k: os.environ.get(k) for k in manifest.get("env", {})}
    try:
        socket.socket.connect = refuse
        socket.socket.connect_ex = refuse
        socket.create_connection = refuse
        requests.get = http.get
        requests.post = http.post
        anthropic.Anthropic = _OfflineAnthropic
        for k, v in manifest.get("env", {}).items():
            os.environ.setdefault(k, v)
        _STATE.update(manifest=manifest, sheets=_SheetClient(books), http=http)
        yield http
    finally:
        socket.socket.connect = saved["connect"]
        socket.socket.connect_ex = saved["connect_ex"]
        socket.create_connection = saved["create_connection"]
        requests.get = saved["get"]
        requests.post = saved["post"]
        anthropic.Anthropic = saved["anthropic"]
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _STATE.clear()
