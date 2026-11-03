"""Phase 4: one ranked set of candidates for Claude, from every source of events.

Daily events (Overtake/Chase/Countdown clusters), weekend events (Takeover/Hold, from committed
history) and Discovery items (verified, proposed patterns) are scored by the transparent ranker;
only the top-ranked (plus exploration) reach Claude, each annotated with its rank, score and
exploration flag. Every scored event gets a decision record in the ledger.
"""

from __future__ import annotations

from faramir import rationale as rationale_mod
from faramir.events.schema import Source
from faramir.learning.rank import rank, score_event


class DiscoveryEvent:
    """A verified Discovery item seen through the ranker's event interface."""

    def __init__(self, item, film: dict):
        self.item = item
        self.strategy = "discovery"
        self.event_id = item.id
        self.fingerprint = item.id
        self.film = film
        self.status = "anticipated"           # a proposed pattern, never a confirmed fact
        self.strength = 0.6
        self.axis = "weekend"
        self.period = item.period
        # This weekend's figures keep their names; an earlier weekend's become previous_<metric>, so a
        # card never shows last weekend's gross as this weekend's.
        own = [c for c in item.claims if c.film_key == item.film_key]
        self.evidence = ({f"previous_{c.metric}": c.value for c in own if c.period != item.period}
                         | {c.metric: c.value for c in own if c.period == item.period} | {item.metric: item.value})
        self.sources = [Source("tn_weekend_history", c.source_row, c.period) for c in item.claims[:2]]
        self.reason_codes = [f"discovery:{item.generator}"]


def _best_signal(film: dict, signals: dict) -> dict | None:
    names = [d.strip() for d in str(film.get("director") or "").split(",") if d.strip()]
    names += list(film.get("lead_cast") or [])[:3] + [film.get("collection_name") or ""]
    order = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    found = [signals[n] for n in names if n and n in signals and "tier" in signals[n]]
    return max(found, key=lambda s: order.get(s["tier"], 0)) if found else None


def event_candidate(e, chart_rank=None, values=None, rationale=None) -> dict:
    return {"kind": "event", "axis": "weekend", "strategy": e.strategy, "event_id": e.event_id,
            "status": e.status, "chart_rank": chart_rank, "event_values": values or dict(e.evidence),
            "rationale": rationale, "threshold_value": None, "comp": None, "angle_categories": [e.strategy],
            "suppressed": False}


def weekend_film(row) -> dict:
    """A payload film for a weekend-chart film that isn't on today's daily chart (weekend stories only)."""
    return {"tmdb_id": row.tmdb_id, "title": row.title, "release_date": "", "days_in_release": row.days_in_release,
            "distributor": "", "director": "", "lead_cast": [], "genres": [], "collection_id": None,
            "collection_name": "", "domestic_yesterday": row.total_gross, "domestic_today": row.total_gross,
            "worldwide_yesterday": None, "worldwide_today": None, "is_signal": False, "candidates": [],
            "source": "weekend_chart"}


def prioritize(payload_films: list[dict], daily_clusters: list, weekend_clusters: list, discovery_items: list,
               films_by_id: dict, signals: dict, estimates: dict, deliveries_by_strategy: dict, headline_recs: dict,
               cfg: dict, run_id: str, slots: int, explored_this_week: int, weekend_rows: list | None = None):
    """``weekend_rows``: the latest weekend chart's rows. A weekend or Discovery story about an identified
    film on that chart reaches the writer even when the film isn't on today's daily chart."""
    entries = []   # (decision, film_tmdb_id, candidate)
    by_film_payload = {pf["tmdb_id"]: pf for pf in payload_films}
    on_weekend = {r.tmdb_id: r for r in (weekend_rows or []) if r.tmdb_id}
    by_key = {r.film_key: r.tmdb_id for r in (weekend_rows or []) if r.tmdb_id}
    extra: dict[int, dict] = {}

    def trackable(tid) -> bool:
        if tid in by_film_payload or tid in extra:
            return True
        if tid in on_weekend:
            extra[tid] = weekend_film(on_weekend[tid])
            return True
        return False
    daily_by_event = {c.get("event_id"): c for pf in payload_films for c in pf["candidates"]}
    for cl in daily_clusters:
        e = cl.primary
        film = films_by_id.get(e.film.get("tmdb_id"), {})
        d = score_event(e, cfg, run_id, _best_signal(film, signals), estimates, deliveries_by_strategy,
                        headline_recs.get(e.event_id), 1.0, cl.secondary, cl.rejected_alternatives)
        entries.append((d, e.film.get("tmdb_id"), daily_by_event.get(e.event_id)))
    for cl in weekend_clusters:
        e = cl.primary
        tid = e.film.get("tmdb_id")
        film = films_by_id.get(tid, {})
        cand = event_candidate(e, e.evidence.get("rank"), rationale=rationale_mod.line(e, cl.secondary))
        d = score_event(e, cfg, run_id, _best_signal(film, signals), estimates, deliveries_by_strategy, None, 1.0,
                        cl.secondary, cl.rejected_alternatives)
        if cl.disposition == "deferred" or not trackable(tid):
            d.disposition = {"value": "filtered", "reason_codes": ["provisional" if cl.disposition == "deferred"
                                                                   else "film_not_active_in_payload"]}
            entries.append((d, None, None))
            continue
        entries.append((d, tid, cand))
    for item in discovery_items:
        tid = None
        for pf in payload_films:
            if item.title and (pf.get("title") or "").lower() == item.title.lower():
                tid = pf["tmdb_id"]
        if tid is None and by_key.get(item.film_key) and trackable(by_key[item.film_key]):
            tid = by_key[item.film_key]
        ev = DiscoveryEvent(item, {"tmdb_id": tid, "title": item.title, "identity_class": "verified" if tid else "legacy"})
        cand = event_candidate(ev, values=ev.evidence, rationale=f"Discovery · {item.why_it_matters}")
        d = score_event(ev, cfg, run_id, None, estimates, deliveries_by_strategy, None, item.novelty)
        if tid is None:
            d.disposition = {"value": "filtered", "reason_codes": ["no_tracked_film_for_discovery_item"]}
            entries.append((d, None, None))
            continue
        entries.append((d, tid, cand))
    rankable = [e for e in entries if e[2] is not None]
    ordered = rank([d for d, _, _ in rankable], slots, cfg, explored_this_week, deliveries_by_strategy)
    keep = {d.event_id for d in ordered if d.disposition["value"] == "suggested"}
    by_event = {d.event_id: (tid, cand) for d, tid, cand in rankable}
    new_payload = []
    for pf in payload_films + list(extra.values()):
        cands = []
        for d in ordered:
            tid, cand = by_event[d.event_id]
            if d.event_id in keep and tid == pf["tmdb_id"]:
                cands.append({**cand, "rank": d.rank, "score": d.score, "exploration": d.exploration["flag"],
                              "floor_fill": "floor_fill" in d.disposition["reason_codes"]})
        new_payload.append({**pf, "candidates": cands})
    decisions = ordered + [d for d, _, cand in entries if cand is None]
    return new_payload, decisions
