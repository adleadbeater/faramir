"""One-line, evidence-only rationale for every delivered event (plan §2: Slack rationale line).

Built deterministically from the event's evidence and sources — never from model output —
so every figure in it is traceable.
"""

from __future__ import annotations

from faramir.events.schema import Event
from faramir.strategies.common import money

LABEL = {"overtake": "Overtake", "chase": "Chase", "countdown": "Countdown", "takeover": "Takeover",
         "hold": "Hold", "streak": "Streak", "legs": "Legs", "exit": "Exit", "studio_battle": "Studio Battle",
         "global_split": "Global Split", "star_record": "Star Record", "dark_horse": "Dark Horse",
         "premium_run": "Premium Run"}


def _src(e: Event) -> str:
    return "; ".join(f"{s.name} {s.as_of}" for s in e.sources)


def _days(n: float) -> str:
    return "1 day" if round(n) == 1 else f"{n:.0f} days"


def line(e: Event, secondary: Event | None = None) -> str:
    ev, t = e.evidence, e.target
    name = f"{t.get('title')} ({t.get('release_year')})" if t.get("type") == "comp" else None
    if e.strategy == "overtake" and name:
        body = (f"{e.axis} {money(ev['current'])} passed {name} at {money(ev['target_value'])}; "
                f"{money(ev['previous'])} the day before")
    elif e.strategy == "overtake":
        body = f"crossed {money(ev['target_value'])} {e.axis}; {money(ev['previous'])} the day before"
    elif e.strategy == "chase":
        body = (f"{money(ev['gap'])} ({ev['gap_pct']:.1%}) behind {name} {e.axis}; about {_days(ev['days_to_pass'])} "
                f"at {money(ev['pace_per_day'])}/day · {ev['meaningful_because'][0]}")
    elif e.strategy == "countdown":
        body = f"{money(ev['gap'])} from {money(ev['target_value'])} {e.axis}; about {_days(ev['days_to_cross'])} at {money(ev['pace_per_day'])}/day"
    else:
        body = e.reason
    status = f" ({e.status})" if e.status in ("provisional", "unverifiable") else ""
    out = f"{LABEL[e.strategy]}{status} · {body} · sources: {_src(e)}"
    if secondary:
        out += f" · also {LABEL[secondary.strategy]}"
    return out
