"""Weekend-chart views for the weekend strategies (Takeover, Hold; later Streak, Legs, Exit,
Dark Horse). Built only from committed weekend history (``faramir.history.read_weekend``):
a weekend that is missing, uncommitted, conflicting or damaged is ``None`` — never assumed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from faramir.events.schema import Source


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class WeekendRow:
    film_key: str            # TN slug (the original film's identity on TN)
    title: str
    source_rank: int | None  # rank as printed by TN; None where TN shows "-"
    derived_rank: int | None
    gross: int | None
    theaters: int | None
    total_gross: int | None
    days_in_release: int | None
    prev_status: str
    engagement: str
    tmdb_id: int | None
    imdb_id: str | None
    identity_confidence: str

    def film(self) -> dict:
        return {"tmdb_id": self.tmdb_id, "imdb_id": self.imdb_id, "title": self.title, "tn_slug": self.film_key,
                "identity_confidence": self.identity_confidence}


@dataclass
class Weekend:
    weekend_start: str
    collection_id: str
    collection_status: str   # provisional | confirmed_by_recollection
    source_url: str
    rows: list[WeekendRow]

    def by_key(self) -> dict[str, WeekendRow]:
        return {r.film_key: r for r in self.rows}

    def ranked(self, n: int) -> WeekendRow | None:
        return next((r for r in self.rows if r.source_rank == n), None)

    def source(self) -> Source:
        return Source("tn_weekend_history", f"{self.collection_id} ({self.source_url})", self.weekend_start)


def from_snapshot(snap) -> Weekend | None:
    """A ``history.Snapshot`` → ``Weekend``; anything not ``available`` is None (unknown)."""
    if snap is None or snap.status != "available":
        return None
    rows = [WeekendRow(
        film_key=r["source_film_key"], title=r["title_reported"], source_rank=_int(r["source_rank"]),
        derived_rank=_int(r["derived_rank"]), gross=_int(r["weekend_gross"]), theaters=_int(r["theaters"]),
        total_gross=_int(r["total_gross"]), days_in_release=_int(r["days_in_release"]), prev_status=r["prev_status"],
        engagement=r["engagement"], tmdb_id=_int(r["tmdb_id"]), imdb_id=r["imdb_id"] or None,
        identity_confidence=r["identity_confidence"]) for r in snap.rows]
    src = snap.rows[0]["source_url"] if snap.rows else ""
    return Weekend(snap.weekend_start, snap.collection_id, snap.collection_status or "provisional", src, rows)


def previous_friday(weekend_start: str) -> str:
    return (date.fromisoformat(weekend_start) - timedelta(days=7)).isoformat()


def is_opener(row: WeekendRow, prior: Weekend) -> bool:
    """Opened this weekend: TN marks it new, or it is in its first 3 days and absent from last weekend."""
    return row.prev_status == "new" or (
        (row.days_in_release or 99) <= 3 and row.film_key not in prior.by_key())
