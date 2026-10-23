"""Studio Battle (strategy 10): distributor year-to-date race.

Fires when a distributor's year-to-date rank changes within the top 3 [CAL], or its YTD total
crosses a threshold [CAL]. Needs a distributor YTD source and a canonical name map — **neither
exists** (TN charts carry no distributor column), so the strategy is disabled
(``blocked_no_source``). The detector is complete and tested on synthetic YTD tables; an
incomplete YTD table makes events ``unverifiable``.
"""

from __future__ import annotations

from faramir.events.schema import Event, Source
from faramir.strategies.common import DetectorResult, money

TOP = 3
THRESHOLDS = [1_000_000_000, 2_000_000_000, 3_000_000_000]
CANONICAL = {"walt disney": "Disney", "disney": "Disney", "buena vista": "Disney", "warner bros.": "Warner Bros.",
             "warner bros": "Warner Bros.", "universal": "Universal", "universal pictures": "Universal",
             "sony pictures": "Sony", "sony": "Sony", "columbia": "Sony", "paramount": "Paramount",
             "paramount pictures": "Paramount", "lionsgate": "Lionsgate", "a24": "A24", "neon": "Neon"}


def canonical(name: str) -> str:
    return CANONICAL.get((name or "").strip().lower(), (name or "").strip())


def _merge(table: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in table:
        out[canonical(r["distributor"])] = out.get(canonical(r["distributor"]), 0) + float(r["ytd_gross"])
    return out


def detect(current: list[dict] | None, previous: list[dict] | None, period: str, source_ref: str | None = None,
           complete: bool = True) -> DetectorResult:
    out = DetectorResult()
    if current is None or previous is None:
        out.rejected.append({"strategy": "studio_battle", "film_key": None, "film": None, "axis": "ytd", "target": "-",
                             "reason_code": "blocked_no_source", "detail": "no distributor year-to-date source"})
        return out
    cur, prev = _merge(current), _merge(previous)
    rank_now = {d: i + 1 for i, (d, _) in enumerate(sorted(cur.items(), key=lambda kv: -kv[1]))}
    rank_before = {d: i + 1 for i, (d, _) in enumerate(sorted(prev.items(), key=lambda kv: -kv[1]))}
    for d, v in cur.items():
        why, target = [], {"type": "distributor", "id": d}
        if rank_now[d] <= TOP and rank_before.get(d) and rank_now[d] < rank_before[d]:
            why.append(f"moved from No. {rank_before[d]} to No. {rank_now[d]} year to date")
        crossed = [t for t in THRESHOLDS if prev.get(d, 0) < t <= v]
        if crossed:
            why.append(f"crossed {money(crossed[-1])} year to date")
        if not why:
            continue
        out.emit(Event(
            strategy="studio_battle", film={"tn_slug": f"distributor:{d}", "title": d}, period=period, axis="ytd",
            target=target, status="confirmed" if complete else "unverifiable",
            evidence={"ytd_gross": v, "previous_ytd_gross": prev.get(d), "rank": rank_now[d], "previous_rank": rank_before.get(d)},
            sources=[Source("distributor_ytd", source_ref or "synthetic", period)],
            reason=f"{d} " + " and ".join(why) + ".", reason_codes=["ytd_rank_change" if "moved" in why[0] else "ytd_threshold"],
            strength=0.6))
    return out
