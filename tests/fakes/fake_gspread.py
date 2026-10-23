"""In-memory stand-in for the parts of gspread that Faramir uses.

Every call is recorded in ``FakeClient.calls`` as ``(sheet_title, method, args)``
so tests can assert on write order and on the absence of writes. Failures can be
injected per method with ``FakeWorksheet.fail_on(method, exc, after=n)``.
"""

from __future__ import annotations

import re
import warnings

import gspread
import requests


def make_api_error(code: int = 503, message: str = "backend error") -> gspread.exceptions.APIError:
    """Build a real gspread APIError without a network response."""
    resp = requests.models.Response()
    resp.status_code = code
    resp._content = (
        '{"error": {"code": %d, "message": "%s", "status": "UNAVAILABLE"}}' % (code, message)
    ).encode()
    return gspread.exceptions.APIError(resp)


_CELL_RE = re.compile(r"^([A-Z]+)(\d+)?$")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parse_cell(ref: str) -> tuple[int, int | None]:
    """'B3' -> (row 2, col 1); 'B' -> (None, col 1) is not supported."""
    m = _CELL_RE.match(ref)
    if not m:
        raise ValueError(f"unsupported A1 reference: {ref}")
    col = _col_index(m.group(1))
    row = int(m.group(2)) - 1 if m.group(2) else None
    return row, col


def _parse_range(a1: str) -> tuple[int, int, int | None, int | None]:
    """Return (row0, col0, row1, col1) inclusive; row1/col1 None means open-ended."""
    a1 = a1.split("!")[-1]
    if ":" in a1:
        start, end = a1.split(":", 1)
        r0, c0 = _parse_cell(start)
        r1, c1 = _parse_cell(end)
        return r0 or 0, c0, r1, c1
    r0, c0 = _parse_cell(a1)
    return r0 or 0, c0, None, None


def _numericise(value):
    if isinstance(value, str):
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?\d+\.\d+", value):
            return float(value)
    return value


class FakeWorksheet:
    def __init__(self, client: "FakeClient", title: str, rows: list[list] | None = None):
        self._client = client
        self.title = title
        self.rows: list[list[str]] = [list(map(str, r)) for r in (rows or [])]
        self._failures: dict[str, list] = {}

    # ── failure injection ────────────────────────────────────────────────
    def fail_on(self, method: str, exc: Exception, after: int = 0, partial: int | None = None) -> None:
        """Raise ``exc`` on the (after+1)-th call to ``method``.

        For ``update``, ``partial=k`` first writes the first k rows of the
        values (a write interrupted part-way), then raises.
        """
        self._failures[method] = [after, exc, partial]

    def _record(self, method: str, *args, on_fail=None) -> None:
        self._client.calls.append((self.title, method, args))
        spec = self._failures.get(method)
        if spec is not None:
            if spec[0] == 0:
                del self._failures[method]
                if on_fail is not None and spec[2] is not None:
                    on_fail(spec[2])
                raise spec[1]
            spec[0] -= 1

    # ── reads ────────────────────────────────────────────────────────────
    def get_all_values(self) -> list[list[str]]:
        self._record("get_all_values")
        return [list(r) for r in self.rows]

    def row_values(self, row: int) -> list[str]:
        self._record("row_values", row)
        if row - 1 >= len(self.rows):
            return []
        values = list(self.rows[row - 1])
        while values and values[-1] == "":  # gspread omits trailing blanks
            values.pop()
        return values

    def get_all_records(self, **kwargs) -> list[dict]:
        self._record("get_all_records")
        if not self.rows:
            return []
        header = self.rows[0]
        out = []
        for r in self.rows[1:]:
            padded = list(r) + [""] * (len(header) - len(r))
            out.append({h: _numericise(v) for h, v in zip(header, padded)})
        return out

    # ── writes ───────────────────────────────────────────────────────────
    def _ensure(self, nrows: int, ncols: int) -> None:
        while len(self.rows) < nrows:
            self.rows.append([])
        for r in self.rows:
            if len(r) < ncols:
                r.extend([""] * (ncols - len(r)))

    def _write_block(self, row0: int, col0: int, values: list[list]) -> None:
        width = max((len(v) for v in values), default=0)
        self._ensure(row0 + len(values), col0 + width)
        for i, vals in enumerate(values):
            for j, v in enumerate(vals):
                self.rows[row0 + i][col0 + j] = "" if v is None else str(v)

    def append_row(self, values, **kwargs) -> None:
        self._record("append_row", list(values))
        self.rows.append(["" if v is None else str(v) for v in values])

    def append_rows(self, values, **kwargs) -> None:
        """``fail_on("append_rows", exc, partial=k)`` appends the first k rows
        and then raises — k == len(values) models an append that succeeded on
        Google's side but timed out before the client saw the response."""
        values = [list(v) for v in values]

        def land(k):
            for v in values[:k]:
                self.rows.append(["" if x is None else str(x) for x in v])

        self._record("append_rows", values, on_fail=land)
        land(len(values))

    def update(self, values=None, range_name=None, **kwargs):
        # Mirror gspread 6.x: legacy (range, values) order is swapped with a warning.
        if isinstance(range_name, (list, tuple)) and isinstance(values, str):
            warnings.warn("legacy worksheet.update() argument order", DeprecationWarning, stacklevel=2)
            range_name, values = values, range_name
        range_name = range_name or "A1"
        values = [list(v) for v in values]
        row0, col0, _, _ = _parse_range(range_name)
        self._record("update", range_name, values, on_fail=lambda k: self._write_block(row0, col0, values[:k]))
        self._write_block(row0, col0, values)
        return {"updatedRange": f"{self.title}!{range_name}"}

    def batch_update(self, data: list[dict], **kwargs):
        """``partial=k`` applies the first k range updates, then raises."""
        data = [dict(d) for d in data]

        def apply(k):
            for d in data[:k]:
                row0, col0, _, _ = _parse_range(d["range"])
                self._write_block(row0, col0, [list(v) for v in d["values"]])

        self._record("batch_update", data, on_fail=apply)
        apply(len(data))
        return {"totalUpdatedCells": sum(len(v) for d in data for v in d["values"])}

    def batch_clear(self, ranges: list[str]) -> None:
        self._record("batch_clear", list(ranges))
        for rng in ranges:
            r0, c0, r1, c1 = _parse_range(rng)
            r1 = len(self.rows) - 1 if r1 is None else r1
            for r in range(r0, min(r1, len(self.rows) - 1) + 1):
                hi = len(self.rows[r]) - 1 if c1 is None else c1
                for c in range(c0, min(hi, len(self.rows[r]) - 1) + 1):
                    self.rows[r][c] = ""
        # Trailing all-blank rows behave like empty rows for reads.
        while self.rows and not any(self.rows[-1]):
            self.rows.pop()

    def clear(self) -> None:
        self._record("clear")
        self.rows = []

    # ── helpers for assertions ───────────────────────────────────────────
    @property
    def data(self) -> list[list[str]]:
        return [list(r) for r in self.rows]


class FakeSpreadsheet:
    def __init__(self, client: "FakeClient", key: str):
        self._client = client
        self.id = key
        self._sheets: dict[str, FakeWorksheet] = {}
        self._failures: dict[str, Exception] = {}
        self.add_worksheet_failure: Exception | None = None

    def add_tab(self, title: str, rows: list[list] | None = None) -> FakeWorksheet:
        ws = FakeWorksheet(self._client, title, rows)
        self._sheets[title] = ws
        return ws

    def worksheet(self, title: str) -> FakeWorksheet:
        self._client.calls.append((title, "worksheet", ()))
        if title in self._failures:
            raise self._failures.pop(title)
        if title not in self._sheets:
            raise gspread.WorksheetNotFound(title)
        return self._sheets[title]

    def fail_worksheet(self, title: str, exc: Exception) -> None:
        self._failures[title] = exc

    def add_worksheet(self, title: str, rows: int = 1000, cols: int = 26, **kwargs) -> FakeWorksheet:
        self._client.calls.append((title, "add_worksheet", (rows, cols)))
        if self.add_worksheet_failure is not None:
            exc, self.add_worksheet_failure = self.add_worksheet_failure, None
            raise exc
        return self.add_tab(title)


class FakeClient:
    """Drop-in for the object returned by ``get_sheet_client()``."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._books: dict[str, FakeSpreadsheet] = {}
        self._open_failures: dict[str, Exception] = {}

    def book(self, key: str) -> FakeSpreadsheet:
        if key not in self._books:
            self._books[key] = FakeSpreadsheet(self, key)
        return self._books[key]

    def fail_open(self, key: str, exc: Exception) -> None:
        self._open_failures[key] = exc

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        self.calls.append((key, "open_by_key", ()))
        if key in self._open_failures:
            raise self._open_failures.pop(key)
        if key not in self._books:
            raise gspread.SpreadsheetNotFound(key)
        return self._books[key]

    WRITE_METHODS = {"append_row", "append_rows", "update", "batch_update", "batch_clear", "clear", "add_worksheet"}

    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[1] in self.WRITE_METHODS]
