"""Older films' grosses restated in today's dollars, so a modern film is never compared with a classic on raw dollars.

A 1969 ticket cost $1.44 and a 2024 one $11.31: Butch Cassidy's $102M at release is about $800M today, and a
horror film at $97M is nowhere near it. Every film released 25 or more years before the data date is "classic-era"
(``CLASSIC_AGE``). ``apply`` restates each one's lifetime grosses in reference-year dollars using the US average ticket
price (data/ticket_prices.json, sourced per year), keeps the grosses at release as ``*_nominal``, and records the
method in ``comp["adjusted"]``, so every card can say exactly what was done. Classic-era films with no verified
price (released before 1955) stop being comparison films: stating their raw dollars would mislead.

The adjustment uses US ticket prices. It is exact enough for domestic grosses and approximate for worldwide ones
(prices abroad differ), and the card says so.
"""

from __future__ import annotations

import json
import os
from datetime import date

PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ticket_prices.json")
CLASSIC_AGE = 25          # [CAL] years before the data date at which a film is "classic" for Modern vs. Classic
_TABLE: dict | None = None


def table() -> dict:
    global _TABLE
    if _TABLE is None:
        with open(PATH, encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def reference() -> tuple[int, float]:
    r = table()["reference"]
    return int(r["year"]), float(r["price"])


def price(year) -> float | None:
    try:
        row = table()["prices"].get(str(int(year)))
    except (TypeError, ValueError):
        return None
    return float(row["price"]) if row else None


def factor(year) -> float | None:
    """Multiplier from the release year's dollars to reference-year dollars; None if there is no verified price."""
    p = price(year)
    return reference()[1] / p if p else None


def adjust(value, year) -> int | None:
    f = factor(year)
    return None if value is None or f is None else int(round(float(value) * f))


def classic_cutoff(data_date: date | str) -> int:
    """The latest release year that counts as classic-era on ``data_date``."""
    y = data_date.year if isinstance(data_date, date) else int(str(data_date)[:4])
    return y - CLASSIC_AGE


def apply(films: list[dict], data_date: date | str) -> dict:
    """Restate classic-era films' grosses in reference-year dollars, in place (idempotent). Returns counts."""
    cutoff = classic_cutoff(data_date)
    ref_year, ref_price = reference()
    done = unavailable = 0
    for f in films:
        if f.get("era") == "classic":
            continue
        try:
            year = int(str(f.get("release_year") or "0")[:4])
        except ValueError:
            continue
        if not year or year > cutoff:
            continue
        fac = factor(year)
        f["era"] = "classic"
        if fac is None:
            f["adjusted"] = None
            f["domestic_status"] = f["worldwide_status"] = "unavailable"     # drops out of every comparison index
            unavailable += 1
            continue
        for axis in ("domestic", "worldwide"):
            nominal = f.get(f"{axis}_lifetime")
            f[f"{axis}_nominal"] = nominal
            if nominal:
                f[f"{axis}_lifetime"] = int(round(nominal * fac))
        f["adjusted"] = {"factor": round(fac, 4), "price_then": price(year), "price_now": ref_price, "year_now": ref_year,
                         "release_year": year}
        done += 1
    return {"adjusted": done, "unavailable": unavailable, "cutoff_year": cutoff, "reference_year": ref_year}


def money(v) -> str:
    v = float(v)
    return f"${v / 1e9:.2f}B" if v >= 1e9 else f"${v / 1e6:.1f}M"


def method_note(comp: dict, axis: str | None = None) -> str | None:
    """One sentence for the card: what was adjusted, how, and by how much."""
    a = comp.get("adjusted")
    if not a:
        return None
    axes = [axis] if axis else ["domestic", "worldwide"]
    parts = [f"{money(comp[f'{x}_nominal'])} {x} at release = about {money(comp[f'{x}_lifetime'])} in {a['year_now']} dollars"
             for x in axes if comp.get(f"{x}_nominal")]
    if not parts:
        return None
    approx = " Worldwide is approximate (US ticket prices)." if "worldwide" in axes and any("worldwide" in p for p in parts) else ""
    return (f"{comp.get('title')} ({a['release_year']}): " + "; ".join(parts) + f" (US average ticket ${a['price_then']:.2f} in "
            f"{a['release_year']} vs ${a['price_now']:.2f} in {a['year_now']})." + approx)
