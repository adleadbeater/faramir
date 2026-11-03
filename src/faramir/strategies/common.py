"""Shared helpers for the deterministic strategy detectors (plan §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from faramir.corpus import reported
from faramir.events.schema import Event, Source, checked, film_key, target_key


def money(v) -> str:
    v = float(v)
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    return f"${v / 1e6:.1f}M"


IDENTITY_CLASS = {"high": "verified", "medium": "verified", "manual": "manual", "legacy": "legacy", "": "legacy"}


def film_ref(film: dict) -> dict:
    return {"tmdb_id": film.get("tmdb_id"), "imdb_id": film.get("imdb_id") or None,
            "title": film.get("canonical_title") or film.get("title"),
            "identity_class": IDENTITY_CLASS.get(str(film.get("identity_confidence") or ""), "legacy"),
            "tn_slug": film.get("tn_slug") or None}


def comp_ref(comp: dict) -> dict:
    return {"type": "comp", "tmdb_id": comp.get("tmdb_id"), "imdb_id": comp.get("imdb_id"),
            "title": comp.get("title"), "release_year": comp.get("release_year"),
            "identity_status": comp.get("identity_status")}


def series(film: dict, axis: str) -> tuple[float, float, int]:
    """(previous cumulative, current cumulative, days the current figure has been unchanged)."""
    prev = float(film.get(f"{axis}_yesterday") or 0)
    cur = float(film.get(f"{axis}_today") or 0)
    stale = int(film.get("dom_stale_days" if axis == "domestic" else "ww_stale_days") or 0)
    return prev, cur, stale


def axis_source(film: dict, axis: str, data_date: str) -> Source:
    if axis == "domestic":
        return Source("tn_daily", f"https://www.the-numbers.com/box-office-chart/daily/{data_date.replace('-', '/')}", data_date)
    return Source("bom_title", f"https://www.boxofficemojo.com/title/{film.get('imdb_id') or '?'}/", data_date)


def corpus_source(gross_as_of: str | None) -> Source:
    return Source("corpus_v2", "data/corpus.v2.json", gross_as_of or "unknown")


def pace(film: dict, axis: str, observations: list[dict] | None = None) -> tuple[float | None, str]:
    """Per-day pace and its basis. Uses the longest window up to 7 days in the film's observation
    series (ledger), else the 1-day change between the last two state values."""
    obs = sorted((o for o in (observations or []) if o.get("axis") == axis and o.get("film_key") == film_key(film)),
                 key=lambda o: o["date"])
    if len(obs) >= 2:
        last = obs[-1]
        window = [o for o in obs if (date.fromisoformat(last["date"]) - date.fromisoformat(o["date"])).days <= 7]
        first = window[0]
        days = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days
        if days > 0 and last["value"] > first["value"]:
            return (last["value"] - first["value"]) / days, f"{days}_day_average"
    prev, cur, _ = series(film, axis)
    if prev > 0 and cur > prev:
        return cur - prev, "1_day_change"
    return None, "no_pace"


def all_time_rank(value: float, corpus: list[dict], axis: str, exclude_tmdb=None) -> int:
    """1 + the number of verified corpus films with a larger reported gross on this axis."""
    return 1 + sum(1 for f in corpus if f.get("tmdb_id") != exclude_tmdb
                   and f.get("identity_status", "verified") == "verified"
                   and (reported(f, axis) or 0) > value)


MEANINGFUL_CATEGORIES = {
    "franchise_sibling": "franchise sibling",
    "creative_head_to_head_director": "same director",
    "creative_head_to_head_cast": "shares a top-billed actor",
    "genre_milestone": "a top-5 film in a shared genre",
    "unexpected_giant_killer": "a notable film (Best Picture, classic or $500M+ worldwide)",
    "classic_same_genre": "a classic in the film's own main genre",
}
ALL_TIME_TOP_N = 100
CATEGORY_STRENGTH = {"franchise_sibling": 0.25, "creative_head_to_head_director": 0.2, "creative_head_to_head_cast": 0.15,
                     "genre_milestone": 0.15, "unexpected_giant_killer": 0.25, "classic_same_genre": 0.2}


def _notable_because(comp: dict) -> str:
    """The actual reason a comparison film counts as notable, not the generic rule."""
    bits = []
    if comp.get("is_best_picture"):
        bits.append("a Best Picture winner")
    if comp.get("is_classic"):
        bits.append(f"a {comp.get('release_year')} classic")
    ww = comp.get("worldwide_lifetime")
    if ww and ww > 500_000_000 and not bits:
        bits.append(f"grossed over $500M worldwide" + (" in today's dollars" if comp.get("adjusted") else ""))
    return " and ".join(bits) or "a notable film"


def meaningful(categories: list[str], comp_rank: int | None, comp: dict | None = None) -> list[str]:
    out = [(_notable_because(comp) if c == "unexpected_giant_killer" and comp else MEANINGFUL_CATEGORIES[c])
           for c in categories if c in MEANINGFUL_CATEGORIES]
    if comp_rank is not None and comp_rank <= ALL_TIME_TOP_N:
        out.append(f"all-time top {ALL_TIME_TOP_N} (rank {comp_rank})")
    return out


KNOWN_RANK = 300   # [CAL] a comparison film inside the all-time top 300 on that axis is widely known


def comp_recognized(comp: dict, categories: list[str], comp_rank: int | None) -> bool:
    """Would a reader recognise the film it passed? A creative/franchise link, a notable film, or an
    all-time top-300 gross. Passing an obscure film is true but not a story."""
    return (bool(set(categories) & set(MEANINGFUL_CATEGORIES)) or (comp_rank is not None and comp_rank <= KNOWN_RANK)
            or bool(comp.get("is_best_picture")) or bool(comp.get("is_classic")))


def scale_strength(total_gross: float | None, base: float) -> float:
    """Box-office scale [CAL]: a story about a small film counts for less."""
    t = total_gross or 0
    return round(base - 0.25 if t < 25e6 else base - 0.1 if t < 75e6 else base, 3)


def comp_strength(categories: list[str], comp_rank: int | None, base: float = 0.5) -> float:
    s = base + sum(CATEGORY_STRENGTH.get(c, 0.0) for c in categories)
    if comp_rank is not None and comp_rank <= ALL_TIME_TOP_N:
        s += 0.1
    return round(min(s, 1.0), 3)


def threshold_strength(value: float) -> float:
    if value >= 1e9:
        return 0.95
    if value >= 5e8:
        return 0.8
    if value >= 2.5e8:
        return 0.7
    return 0.55


@dataclass
class DetectorResult:
    events: list[Event] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def reject(self, strategy: str, film: dict, axis: str, target: dict, code: str, detail: str) -> None:
        self.rejected.append({"strategy": strategy, "film_key": film_key(film_ref(film)),
                              "film": film_ref(film).get("title"), "axis": axis, "target": target_key(target),
                              "reason_code": code, "detail": detail})

    def emit(self, event: Event) -> None:
        self.events.append(checked(event))

    def extend(self, other: "DetectorResult") -> "DetectorResult":
        self.events += other.events
        self.rejected += other.rejected
        return self
