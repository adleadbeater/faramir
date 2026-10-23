"""Append-only JSONL evidence ledger (plan §1 storage decision).

Records are appended to ``<root>/<stream>/<YYYY-MM>.jsonl``. Appends are idempotent: a
record whose ``id`` already exists in the stream is not written again. Nothing is ever
rewritten or removed.

Streams used: ``events`` (every detected event with its disposition), ``candidates``
(every matcher/strategy candidate with its disposition), ``deliveries`` (events sent to
Slack), ``observations`` (per-film cumulative grosses, for pace), ``editorial`` (status
changes), and from later phases ``discovery`` and ``decisions``.

The root is the run's artifact directory by default (``out/<run_id>/ledger``) or
``FARAMIR_LEDGER_DIR`` for a persistent local ledger. Where the production ledger lives
(a dedicated branch committed by Actions, plan §1) is a deployment decision; nothing here
writes outside the local filesystem.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


class Ledger:
    """``root`` receives appends; ``read_roots`` (optional) are additionally read, never written —
    a dry run reads the persistent ledger but writes only into its own artifact directory."""

    def __init__(self, root: str, read_roots: list[str] | None = None):
        self.root = root
        self.read_roots = [r for r in (read_roots or []) if r and os.path.abspath(r) != os.path.abspath(root)]

    def _path(self, stream: str, month: str) -> str:
        return os.path.join(self.root, stream, f"{month}.jsonl")

    def read(self, stream: str) -> list[dict]:
        out, seen = [], set()
        for root in self.read_roots + [self.root]:
            d = os.path.join(root, stream)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.endswith(".jsonl"):
                    with open(os.path.join(d, name), encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                r = json.loads(line)
                                if r.get("id") not in seen:
                                    seen.add(r.get("id"))
                                    out.append(r)
        return out

    def ids(self, stream: str) -> set[str]:
        return {r.get("id") for r in self.read(stream)}

    def append(self, stream: str, records: list[dict], month: str | None = None) -> int:
        """Append records that are not already in the stream (by ``id``). Returns the count written."""
        if not records:
            return 0
        existing = self.ids(stream)
        month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        new = []
        for r in records:
            if not r.get("id"):
                raise ValueError(f"ledger record in {stream!r} has no id")
            if r["id"] in existing:
                continue
            existing.add(r["id"])
            new.append(r)
        if not new:
            return 0
        path = self._path(stream, month)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
        return len(new)


def default_ledger(out_dir: str | None, dry_run: bool = False) -> Ledger:
    """Live: append to FARAMIR_LEDGER_DIR (persistent) or the run's artifact directory.
    Dry run: read FARAMIR_LEDGER_DIR, append only to the artifact directory."""
    local = os.path.join(out_dir or "out", "ledger")
    persistent = os.environ.get("FARAMIR_LEDGER_DIR")
    if dry_run:
        return Ledger(local, read_roots=[persistent] if persistent else None)
    return Ledger(persistent or local)
