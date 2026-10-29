"""Titles written by the Aragorn method: Aragorn's own system prompt (config/titles/, copied from the
aragorn agent with its source hash) plus Faramir's box-office section built from the MovieWeb Title
Crafting Rules. One Claude call per story, same model and settings as Aragorn. Every option is fact
checked (titles.check); the best passing option is the title, the next passing one the alternative.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

from faramir.titles.check import check

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CONFIG = os.path.join(ROOT, "config", "titles")
MODEL = "claude-sonnet-4-6"          # Aragorn's model (aragorn.config.yaml)
MAX_TOKENS = 3072


def system_prompt() -> str:
    aragorn = open(os.path.join(CONFIG, "aragorn_system_prompt.md"), encoding="utf-8").read()
    addendum = open(os.path.join(CONFIG, "box_office_addendum.md"), encoding="utf-8").read()
    return aragorn.rstrip() + "\n\n" + addendum


def aragorn_prompt_drift(aragorn_repo_prompt: str | None) -> str | None:
    """If Aragorn's live prompt file differs from Faramir's copy, say so (a reminder to re-copy it)."""
    if not aragorn_repo_prompt or not os.path.exists(aragorn_repo_prompt):
        return None
    sources = json.load(open(os.path.join(CONFIG, "SOURCES.json")))
    live = hashlib.sha256(open(aragorn_repo_prompt, "rb").read()).hexdigest()
    return None if live == sources["aragorn_system_prompt.md"]["sha256"] else "Aragorn's prompt has changed since Faramir copied it"


@dataclass
class Titled:
    title: str | None
    alternative: str | None
    options: list[dict] = field(default_factory=list)     # every option with its check result
    verdict: str = "ok"                                    # ok | kill | none_passed | error
    note: str = ""
    usage: dict = field(default_factory=dict)


def _parse(text: str) -> list[dict]:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = t.find("["), t.rfind("]")
    return json.loads(t[start:end + 1]) if start >= 0 else []


MAX_TRIMS = 2
TRIM_PROMPT = """You shorten MovieWeb News titles to at most {n} characters. Follow the MovieWeb Title Crafting Rules
trimming order: cut the untested word first, the redundant fact second, the genre modifier third. Never cut
"Officially", never shorten "Action Thriller", and never strip the genre from a masked film ("a Best
Picture-Winning Crime Thriller" keeps "Crime Thriller"). Keep the same facts, the same named films and
people, and the same tense. Add nothing.
Single clause, no dashes. Return JSON only: {{"title": "..."}}"""


def _trim(client, title: str) -> tuple[str | None, dict]:
    from faramir.titles.check import MAX_CHARS
    try:
        resp = client.messages.create(model=MODEL, max_tokens=200, system=TRIM_PROMPT.format(n=MAX_CHARS - 3),
                                      messages=[{"role": "user", "content": title}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return json.loads(text[text.find("{"): text.rfind("}") + 1]).get("title"), usage
    except Exception:  # noqa: BLE001
        return None, {}


def write(brief, client, other_titles: list[str]) -> Titled:
    try:
        # Aragorn sets temperature 0.3 in its Node client; Faramir's pinned Python SDK takes the model default.
        resp = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, system=system_prompt(), messages=[{"role": "user", "content": brief.user_message()}])
        text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        options = _parse(text)
    except Exception as exc:  # noqa: BLE001 — a failed title never blocks the card
        return Titled(None, None, verdict="error", note=str(exc)[:200])
    if any(str(o.get("mechanism", "")).upper() == "KILL" for o in options):
        kill = next(o for o in options if str(o.get("mechanism", "")).upper() == "KILL")
        return Titled(None, None, options, "kill", kill.get("logic", ""), usage)
    passing, trims = [], 0
    for o in options:
        c = check(str(o.get("title", "")), brief, other_titles)
        if not c.ok and all(x.startswith("too_long") for x in c.reasons) and trims < MAX_TRIMS:
            trims += 1
            short, u = _trim(client, str(o["title"]))
            for k in usage:
                usage[k] += u.get(k, 0)
            if short:
                o["original_title"], o["title"] = o["title"], short
                c = check(short, brief, other_titles)
        o["check"] = {"ok": c.ok, "reasons": c.reasons, "warnings": c.warnings}
        if c.ok:
            passing.append(o)
    if not passing:
        return Titled(None, None, options, "none_passed", "no option passed the fact check", usage)
    return Titled(passing[0]["title"], passing[1]["title"] if len(passing) > 1 else None, options, "ok",
                  passing[0].get("logic", ""), usage)
