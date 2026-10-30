"""Versioned priority config (plan §6): loaded from ``config/priority_config.json``. Proposed
changes are diffs with reasons, recorded in the ledger; a proposal never applies itself — an
approved change is a new committed config version (and can be rolled back)."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone

PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "priority_config.json")


def load(path: str | None = None) -> dict:
    with open(path or PATH, encoding="utf-8") as f:
        return json.load(f)


def _diff(a, b, prefix=""):
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            out += _diff(va, vb, f"{prefix}{k}.")
        elif va != vb:
            out.append({"key": f"{prefix}{k}", "from": va, "to": vb})
    return out


def propose(current: dict, changes: dict, reasons: list[str], new_version: str) -> dict:
    """A proposal record (for the ledger): the full proposed config, the diff and the reasons."""
    proposed = copy.deepcopy(current)
    for dotted, value in changes.items():
        node = proposed
        *path, last = dotted.split(".")
        for p in path:
            node = node[p]
        node[last] = value
    proposed["version"] = new_version
    proposed["status"] = "proposed — needs human approval"
    return {"id": f"priority_config:{new_version}", "from_version": current["version"], "to_version": new_version,
            "diff": _diff(current, proposed), "reasons": reasons, "proposed": proposed, "applied": False,
            "proposed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
