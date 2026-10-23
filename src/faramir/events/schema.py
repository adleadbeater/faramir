"""The event: one detected, evidence-backed story opportunity (plan §1, §3, §6).

Every event carries:

* a **stable id** — ``event_id`` = hash(strategy, film, period, axis, target). The same
  opportunity detected again on the same period gets the same id;
* a **fingerprint** — hash(event_id + evidence values). It changes when the underlying
  figures change, so "the same anticipated event, unchanged" is detectable;
* **sources** — where each figure came from (source name, reference, as-of date);
* **evidence** — the figures the classification rests on;
* an explainable **reason** and machine-readable ``reason_codes``.

``validate()`` refuses an event that lacks any of these; detectors only emit valid events.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

STRATEGIES = ("overtake", "chase", "countdown", "takeover", "hold", "streak", "legs", "exit",
              "studio_battle", "global_split", "star_record", "dark_horse", "premium_run")
STATUSES = ("confirmed", "anticipated", "provisional", "unverifiable")
# Order used by the classifier: completed beats anticipated; provisional/unverifiable are never delivered.
STATUS_RANK = {"confirmed": 0, "anticipated": 1, "provisional": 2, "unverifiable": 3}
DELIVERABLE = {"confirmed", "anticipated"}


def _sha(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def film_key(film: dict) -> str:
    """Canonical film identity: IMDb id when known, else the TMDB id."""
    if film.get("imdb_id"):
        return str(film["imdb_id"])
    if film.get("tmdb_id") is not None:
        return f"tmdb:{film['tmdb_id']}"
    return f"slug:{film.get('tn_slug', '')}"


def target_key(target: dict) -> str:
    kind = target.get("type")
    if kind == "comp":
        return f"comp:{target.get('imdb_id') or target.get('tmdb_id')}"
    if kind == "threshold":
        return f"usd:{int(target.get('value') or 0)}"
    return f"{kind}:{target.get('id') or target.get('value')}"


@dataclass
class Source:
    name: str            # tn_daily | tn_weekend_history | bom_title | corpus_v2 | tmdb | state
    ref: str             # URL, chart date or file the figure came from
    as_of: str           # date the figure describes


@dataclass
class Event:
    strategy: str
    film: dict                        # tmdb_id, imdb_id, title, identity_class
    period: str                       # data date (daily strategies) or weekend Friday (weekend strategies)
    axis: str | None
    target: dict                      # {type: comp|threshold|rank|film|..., ...}
    status: str
    evidence: dict                    # the figures the classification rests on
    sources: list[Source]
    reason: str                       # human-readable explanation of the classification
    reason_codes: list[str]
    subtype: str | None = None
    strength: float = 0.0             # event_strength in [0, 1] (plan §6)
    candidate: dict | None = field(default=None, repr=False)  # the bindable candidate, for Claude

    @property
    def event_id(self) -> str:
        return _sha("event", self.strategy, film_key(self.film), self.period, self.axis or "", target_key(self.target))

    @property
    def fingerprint(self) -> str:
        return _sha(self.event_id, json.dumps(self.evidence, sort_keys=True, default=str))

    @property
    def story_key(self) -> str:
        """The opportunity independent of the day it was seen (for cross-day dedup)."""
        return _sha("story", self.strategy, film_key(self.film), self.axis or "", target_key(self.target))

    @property
    def cluster_key(self) -> tuple:
        return (film_key(self.film), self.period, self.axis or "", target_key(self.target))

    def validate(self) -> list[str]:
        problems = []
        if self.strategy not in STRATEGIES:
            problems.append(f"unknown strategy {self.strategy!r}")
        if self.status not in STATUSES:
            problems.append(f"unknown status {self.status!r}")
        if not self.sources or any(not (s.name and s.ref and s.as_of) for s in self.sources):
            problems.append("every event needs at least one complete source (name, ref, as_of)")
        if not self.evidence:
            problems.append("no evidence")
        if not self.reason or not self.reason_codes:
            problems.append("no explainable reason")
        if not (self.film.get("tmdb_id") or self.film.get("imdb_id") or self.film.get("tn_slug")):
            problems.append("film has no identity")
        if not self.period:
            problems.append("no period")
        if not 0.0 <= self.strength <= 1.0:
            problems.append("strength outside [0, 1]")
        return problems

    def to_record(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "candidate"}
        d.update(id=self.event_id, event_id=self.event_id, fingerprint=self.fingerprint, story_key=self.story_key,
                 film_key=film_key(self.film), cluster=list(self.cluster_key))
        return d


class InvalidEvent(ValueError):
    pass


def checked(event: Event) -> Event:
    problems = event.validate()
    if problems:
        raise InvalidEvent(f"{event.strategy} event for {event.film.get('title')!r}: {'; '.join(problems)}")
    return event
