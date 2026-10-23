"""Strategy registry: every one of the 13 strategies (plan §3) plus Discovery, with its data
status. A strategy runs only when ``enabled``; a strategy whose sources don't exist is never
reported as operational with live data."""

from __future__ import annotations

import json
import os

CONFIG = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "strategies.json")


def registry(path: str | None = None) -> dict:
    with open(path or CONFIG, encoding="utf-8") as f:
        return json.load(f)


def enabled(name: str, reg: dict | None = None) -> bool:
    reg = reg or registry()
    entry = reg["strategies"].get(name) or (reg.get("discovery") if name == "discovery" else None)
    return bool(entry and entry.get("enabled"))


def operational_with_live_data(name: str, reg: dict | None = None) -> bool:
    reg = reg or registry()
    entry = reg["strategies"].get(name, {})
    return bool(entry.get("enabled")) and entry.get("data_status") == "live"
