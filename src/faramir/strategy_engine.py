"""Daily strategy engine (Phase 1): detectors → classifier → dedup → payload, with a ledger row
for every candidate and every event.

``run_daily`` replaces each active film's raw matcher/milestone candidate list with one
candidate per delivered cluster (annotated with its event id, strategy and rationale), so
Claude only chooses among classified, deduplicated, evidence-backed events.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from faramir import rationale
from faramir.binding import candidate_id
from faramir.classify import classify
from faramir.corpus import build_index
from faramir.dedup import dedup
from faramir.events.ledger import Ledger
from faramir.events.schema import film_key, target_key
from faramir.strategies import chase, countdown, enabled, global_split, overtake, registry, star_record
from faramir.strategies.common import DetectorResult, film_ref


def _sha(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _cand_target(c: dict) -> str:
    if c.get("comp"):
        return target_key({"type": "comp", "imdb_id": c["comp"].get("imdb_id"), "tmdb_id": c["comp"].get("tmdb_id")})
    return target_key({"type": "threshold", "value": c.get("threshold_value")})


@dataclass
class EngineResult:
    payload_films: list[dict]
    events: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    delivered_clusters: list = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    by_candidate_id: dict = field(default_factory=dict)   # candidate_id -> cluster (for picks)

    def summary(self) -> dict:
        by = {}
        for e in self.events:
            by[e.strategy] = by.get(e.strategy, 0) + 1
        codes = {}
        for r in self.rejected:
            codes[f"{r['strategy']}:{r['reason_code']}"] = codes.get(f"{r['strategy']}:{r['reason_code']}", 0) + 1
        return {"events": by, "clusters": len(self.clusters), "delivered_clusters": len(self.delivered_clusters),
                "deferred": sum(1 for c in self.clusters if c.disposition == "deferred"),
                "suppressed": {s["reason_code"]: sum(1 for x in self.suppressed if x["reason_code"] == s["reason_code"])
                               for s in self.suppressed},
                "rejected": codes}


def _prior_gaps(deliveries: list[dict], fkey: str) -> dict:
    out = {}
    for d in sorted(deliveries, key=lambda d: d["period"]):
        if d["strategy"] == "countdown" and d["film_key"] == fkey:
            out[(d["axis"], int(d["target"]["value"]))] = (d.get("evidence") or {}).get("gap")
    return out


def run_daily(films: list[dict], payload_films: list[dict], corpus: list[dict], data_date: str,
              gross_as_of: str | None, suppressed_pairs: set, ledger: Ledger, reg: dict | None = None,
              tracked: list[dict] | None = None) -> EngineResult:
    reg = reg or registry()
    index = build_index(corpus)
    by_id = {f.get("tmdb_id"): f for f in films}
    observations = ledger.read("observations")
    deliveries = ledger.read("deliveries")
    # Record today's figures for future pace calculations.
    obs = []
    for f in films:
        for axis in ("domestic", "worldwide"):
            v = float(f.get(f"{axis}_today") or 0)
            if v > 0:
                fk = film_key(film_ref(f))
                obs.append({"id": _sha(fk, axis, data_date), "film_key": fk, "axis": axis, "date": data_date, "value": v})
    ledger.append("observations", obs, month=data_date[:7])
    observations = observations + obs

    found = DetectorResult()
    for pf in payload_films:
        film = by_id.get(pf["tmdb_id"]) or pf
        cands = pf.get("candidates") or []
        if enabled("overtake", reg):
            found.extend(overtake.detect(film, cands, corpus, data_date, gross_as_of))
        if enabled("chase", reg):
            found.extend(chase.detect(film, corpus, index, data_date, gross_as_of, suppressed_pairs, observations))
        if enabled("countdown", reg):
            sup_axes = {a for (fid, comp, a) in suppressed_pairs if fid == film.get("tmdb_id") and comp == 0}
            found.extend(countdown.detect(film, data_date, prior_gaps=_prior_gaps(deliveries, film_key(film_ref(film))),
                                          observations=observations, suppressed_axes=sup_axes))
        if enabled("star_record", reg):
            found.extend(star_record.detect(film, corpus, tracked or films, data_date, gross_as_of))
        if enabled("global_split", reg):
            found.extend(global_split.detect(film, corpus, data_date, gross_as_of, rollout_complete=None))

    clusters = classify(found.events)
    qualified = [c for c in clusters if c.disposition == "qualified"]
    kept, suppressed = dedup(qualified, deliveries, data_date)
    result = EngineResult(payload_films=[], events=found.events, clusters=clusters, delivered_clusters=kept,
                          suppressed=suppressed, rejected=found.rejected)

    # Payload: one candidate per delivered cluster, annotated.
    keep_by_film: dict = {}
    for c in kept:
        cand = dict(c.primary.candidate)
        cand.update(event_id=c.primary.event_id, strategy=c.primary.strategy,
                    secondary_strategy=c.secondary.strategy if c.secondary else None,
                    rationale=rationale.line(c.primary, c.secondary))
        keep_by_film.setdefault(c.primary.film["tmdb_id"], []).append(cand)
        comp = cand.get("comp") or {}
        cid = candidate_id(data_date, c.primary.film["tmdb_id"], comp.get("tmdb_id") if comp else None,
                           None if comp else cand.get("threshold_value"), cand["axis"], cand["kind"])
        result.by_candidate_id[cid] = c
    for pf in payload_films:
        result.payload_films.append({**pf, "candidates": keep_by_film.get(pf["tmdb_id"], [])})

    # Ledger: every event with its disposition, and every input candidate with what became of it.
    disp: dict[str, dict] = {}
    for c in clusters:
        disp[c.primary.event_id] = {"disposition": "deferred" if c.disposition == "deferred" else "primary",
                                    "reason_codes": c.reason_codes}
        if c.secondary:
            disp[c.secondary.event_id] = {"disposition": "secondary", "reason_codes": [f"secondary_to:{c.primary.strategy}"]}
        for alt in c.rejected_alternatives:
            disp[alt["event_id"]] = {"disposition": "rejected_alternative", "reason_codes": [alt["reason_code"]]}
    for s in suppressed:
        disp[s["event_id"]] = {"disposition": "suppressed", "reason_codes": [s["reason_code"]]}
    kept_ids = {c.primary.event_id for c in kept}
    for eid, d in disp.items():
        if d["disposition"] == "primary" and eid not in kept_ids:
            d["disposition"] = "suppressed"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger.append("events", [{**e.to_record(), **disp.get(e.event_id, {}), "recorded_at": now} for e in found.events],
                  month=data_date[:7])
    by_target = {}
    for e in found.events:
        by_target.setdefault((film_key(e.film), e.axis, target_key(e.target)), []).append(e.event_id)
    rej_by_target = {}
    for r in found.rejected:
        rej_by_target.setdefault((r["film_key"], r["axis"], r["target"]), []).append(f"{r['strategy']}:{r['reason_code']}")
    cand_rows = []
    for pf in payload_films:
        fk = film_key(film_ref(by_id.get(pf["tmdb_id"]) or pf))
        for cnd in pf.get("candidates") or []:
            key = (fk, cnd["axis"], _cand_target(cnd))
            events = by_target.get(key, [])
            cand_rows.append({"id": _sha(data_date, fk, cnd["axis"], cnd.get("kind"), key[2]), "date": data_date,
                              "film_key": fk, "axis": cnd["axis"], "kind": cnd.get("kind"), "target": key[2],
                              "became_events": events, "rejections": rej_by_target.get(key, []),
                              "disposition": "event" if events else ("rejected" if rej_by_target.get(key) else "not_a_strategy_event")})
    ledger.append("candidates", cand_rows, month=data_date[:7])
    return result


def record_deliveries(ledger: Ledger, clusters: list, data_date: str, suggestion_ids: dict) -> int:
    """After Slack delivery: one ``deliveries`` row per delivered event (drives cross-day dedup)."""
    rows = []
    for c in clusters:
        e = c.primary
        rows.append({"id": e.event_id, "event_id": e.event_id, "story_key": e.story_key, "fingerprint": e.fingerprint,
                     "strategy": e.strategy, "status": e.status, "film_key": film_key(e.film), "axis": e.axis,
                     "target": e.target, "period": data_date, "evidence": e.evidence,
                     "suggestion_id": suggestion_ids.get(e.event_id)})
    return ledger.append("deliveries", rows, month=data_date[:7])
