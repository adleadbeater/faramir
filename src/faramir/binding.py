"""Bind Claude's picks to the candidates Faramir actually generated.

A pick is accepted only when it identifies exactly one real, unsuppressed
candidate:

  comparison pick  (comp_tmdb_id set)   -> candidate (active, comp, axis);
                                           kind must equal the candidate's kind;
                                           a stated threshold must equal the
                                           comp's gross on that axis
  milestone pick   (comp_tmdb_id null)  -> kind "milestone" and a threshold that
                                           is a milestone candidate on that axis
  angle_category                         -> one of the candidate's categories, or
                                           the prompt's family name for them
                                           (creative_head_to_head -> _director/_cast)

Every accepted pick gets a stable ``candidate_id`` (hash of data date + event
identity) and an ``evidence`` snapshot of the verified figures it rests on,
with ``evidence_ref`` = hash of that snapshot. Every rejection carries a reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

CATEGORY_FAMILIES = {
    "creative_head_to_head": {"creative_head_to_head_director", "creative_head_to_head_cast"},
}


def candidate_id(data_date: str, active, comp, threshold, axis: str, kind: str) -> str:
    target = f"C{comp}" if comp is not None else f"M{threshold}"
    return hashlib.sha1(f"{data_date}|{active}|{target}|{axis}|{kind}".encode()).hexdigest()[:16]


@dataclass
class Candidate:
    candidate_id: str
    active_tmdb_id: int
    comp_tmdb_id: int | None
    axis: str
    kind: str
    threshold: int | None
    categories: list[str]
    suppressed: bool
    comp: dict | None
    milestone_type: str | None


@dataclass
class BindingResult:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "accepted": [{"candidate_id": p["candidate_id"], "evidence_ref": p["evidence_ref"],
                          "headline": p.get("headline")} for p in self.accepted],
            "rejected": self.rejected,
        }


def _int(v):
    try:
        return int(v) if v is not None and str(v).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def index_candidates(active_films: list[dict], data_date: str) -> tuple[dict, dict]:
    """(key -> [Candidate]) with key (active, ("C", comp) | ("M", threshold), axis); plus active films by id."""
    index: dict[tuple, list[Candidate]] = {}
    films = {}
    for film in active_films:
        aid = _int(film.get("tmdb_id"))
        films[aid] = film
        for c in film.get("candidates", []):
            comp = c.get("comp") or None
            comp_id = _int(comp.get("tmdb_id")) if comp else None
            threshold = _int(c.get("threshold_value"))
            cand = Candidate(
                candidate_id=candidate_id(data_date, aid, comp_id, threshold if comp_id is None else None, c["axis"], c["kind"]),
                active_tmdb_id=aid, comp_tmdb_id=comp_id, axis=c["axis"], kind=c["kind"], threshold=threshold,
                categories=list(c.get("angle_categories") or []), suppressed=bool(c.get("suppressed")),
                comp=comp, milestone_type=c.get("milestone_type"),
            )
            key = (aid, ("C", comp_id) if comp_id is not None else ("M", threshold), c["axis"])
            index.setdefault(key, []).append(cand)
    return index, films


def _category_ok(category: str, allowed: list[str]) -> bool:
    return category in allowed or bool(CATEGORY_FAMILIES.get(category, set()) & set(allowed))


def _evidence(c: Candidate, film: dict, data_date: str, gross_as_of: str | None) -> dict:
    comp = c.comp
    return {
        "data_date": data_date,
        "corpus_gross_as_of": gross_as_of,
        "event": {"axis": c.axis, "kind": c.kind, "milestone_type": c.milestone_type, "threshold": c.threshold},
        "active": {k: film.get(k) for k in ("tmdb_id", "title", "genres", "director", "lead_cast", "domestic_yesterday",
                                             "domestic_today", "worldwide_yesterday", "worldwide_today", "days_in_release")},
        "comp": None if comp is None else {k: comp.get(k) for k in (
            "imdb_id", "tmdb_id", "title", "release_year", "genres", "director", "lead_cast", "is_best_picture",
            "domestic_lifetime", "domestic_status", "worldwide_lifetime", "worldwide_status", "identity_status")},
        "categories": list(c.categories),
    }


def bind_picks(picks: list[dict], active_films: list[dict], data_date: str, gross_as_of: str | None = None) -> BindingResult:
    index, films = index_candidates(active_films, data_date)
    out = BindingResult()
    taken: set[str] = set()

    def reject(pick: dict, reason: str) -> None:
        out.rejected.append({"pick": {k: pick.get(k) for k in ("active_tmdb_id", "comp_tmdb_id", "axis", "kind",
                                                                 "threshold_value", "angle_category", "headline")},
                             "reason": reason})

    for pick in picks:
        aid, comp, threshold = _int(pick.get("active_tmdb_id")), _int(pick.get("comp_tmdb_id")), _int(pick.get("threshold_value"))
        axis, kind = pick.get("axis"), pick.get("kind")
        other_axis = "worldwide" if axis == "domestic" else "domestic"
        if aid not in films:
            reject(pick, "active film is not in today's payload")
            continue
        if comp is not None:
            if kind == "milestone":
                reject(pick, "kind 'milestone' given with a comparison film")
                continue
            key = (aid, ("C", comp), axis)
            matches = index.get(key, [])
            if not matches:
                if index.get((aid, ("C", comp), other_axis)):
                    reject(pick, f"wrong axis: this comparison is a candidate on {other_axis} only")
                else:
                    reject(pick, "invented comparison: not a candidate for this film")
                continue
        else:
            if kind != "milestone":
                reject(pick, f"kind {kind!r} requires a comparison film")
                continue
            if threshold is None:
                reject(pick, "milestone without a threshold")
                continue
            matches = index.get((aid, ("M", threshold), axis), [])
            if not matches:
                if index.get((aid, ("M", threshold), other_axis)):
                    reject(pick, f"wrong axis: this milestone is a candidate on {other_axis} only")
                else:
                    reject(pick, f"unsupported milestone {threshold} on {axis}")
                continue
        if len(matches) > 1:
            reject(pick, f"ambiguous: {len(matches)} candidates match")
            continue
        c = matches[0]
        if c.kind != kind:
            reject(pick, f"event kind {kind!r} does not match the candidate's {c.kind!r}")
            continue
        if comp is not None and threshold is not None and threshold != c.threshold:
            reject(pick, f"threshold {threshold} does not match the comparison's {c.axis} gross {c.threshold}")
            continue
        if c.suppressed:
            reject(pick, "candidate is suppressed (suggested within the suppression window)")
            continue
        if not _category_ok(str(pick.get("angle_category") or ""), c.categories):
            reject(pick, f"angle_category {pick.get('angle_category')!r} is not supported by the candidate {c.categories}")
            continue
        if c.candidate_id in taken:
            reject(pick, "duplicate: candidate already picked")
            continue
        taken.add(c.candidate_id)
        evidence = _evidence(c, films[aid], data_date, gross_as_of)
        blob = json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str)
        out.accepted.append({
            **pick,
            "candidate_id": c.candidate_id,
            "evidence": evidence,
            "evidence_ref": hashlib.sha1(blob.encode()).hexdigest()[:16],
        })
    return out
