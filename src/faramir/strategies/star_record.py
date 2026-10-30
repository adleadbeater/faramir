"""Star Record (strategy 12): F becomes the career-best film of its director or a top-3-billed actor.

Provability rule (plan §3): if F's gross is above the corpus floor on that axis (the smallest
reported gross in the corpus), every film that grossed more is in the corpus — provided the
corpus passed its integrity checks — so "career-best" is provable against corpus + tracked
films. Below the floor the event is ``unverifiable``. Career totals are never claimed. A person
billed below third on F (a cameo) doesn't count; a previous best on which the person was not
top-3 is labelled ``ensemble``.
"""

from __future__ import annotations

from faramir.corpus import reported
from faramir.events.schema import Event
from faramir.strategies.common import DetectorResult, corpus_source, film_ref, money, series, axis_source


def _people(film: dict) -> list[tuple[str, str]]:
    out = [(d.strip(), "director") for d in str(film.get("director") or "").split(",") if d.strip()]
    out += [(c, "actor") for c in list(film.get("lead_cast") or [])[:3]]
    return out


def _credited(film: dict, name: str, top3: bool = True) -> bool:
    dirs = {d.strip() for d in str(film.get("director") or "").split(",")}
    cast = list(film.get("lead_cast") or [])
    return name in dirs or name in (cast[:3] if top3 else cast)


def detect(film: dict, corpus: list[dict], tracked: list[dict], data_date: str, gross_as_of: str | None,
           corpus_ok: bool = True) -> DetectorResult:
    """tracked: other active/retired films from state (with current grosses and credits)."""
    out = DetectorResult()
    for axis in ("domestic", "worldwide"):
        prev, cur, stale = series(film, axis)
        if cur <= 0:
            continue
        floor = min((reported(f, axis) for f in corpus if reported(f, axis)), default=None)
        for name, role in _people(film):
            target = {"type": "person", "id": name, "role": role}
            others = [(f.get("title"), reported(f, axis), f.get("tmdb_id"), f) for f in corpus
                      if f.get("tmdb_id") != film.get("tmdb_id") and _credited(f, name, top3=False) and reported(f, axis)]
            others += [(t.get("canonical_title") or t.get("title"), float(t.get(f"{axis}_today") or 0), t.get("tmdb_id"), t)
                       for t in tracked if t.get("tmdb_id") != film.get("tmdb_id") and _credited(t, name, top3=False)
                       and float(t.get(f"{axis}_today") or 0) > 0]
            if not others:
                continue
            top_title, top_value, top_id, top_film = max(others, key=lambda x: x[1])
            if cur <= top_value:
                continue
            if floor is None or cur <= floor or not corpus_ok:
                out.reject("star_record", film, axis, target, "unverifiable",
                           f"{money(cur)} is not above the corpus floor {money(floor or 0)} (or the corpus failed integrity)")
                continue
            new_record = prev <= top_value < cur
            ensemble = not _credited(top_film, name, top3=True)
            out.emit(Event(
                strategy="star_record", film=film_ref(film), period=data_date, axis=axis, target=target,
                status="confirmed",
                evidence={"person": name, "role": role, "current": cur, "previous": prev, "previous_best": top_title,
                          "previous_best_tmdb": top_id, "previous_best_value": top_value, "corpus_floor": floor,
                          "crossed_today": new_record, "previous_best_label": "ensemble" if ensemble else "lead",
                          "films_compared": len(others)},
                sources=[axis_source(film, axis, data_date), corpus_source(gross_as_of)],
                reason=(f"{film_ref(film)['title']} ({money(cur)} {axis}) is now the biggest {axis} gross of {name}'s "
                        f"{'directing' if role == 'director' else 'top-billed'} career, passing {top_title} "
                        f"({money(top_value)}); provable because it is above the corpus floor ({money(floor)})."),
                reason_codes=["career_best", role] + (["previous_best_ensemble"] if ensemble else []),
                subtype=role, strength=0.85 if new_record else 0.7))
    return out
