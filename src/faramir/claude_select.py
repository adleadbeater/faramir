"""Anthropic API pick selection for Faramir."""

import json
import logging
from datetime import date

import anthropic

from faramir.config import SELECTION_MODEL, SELECTION_MAX_TOKENS

logger = logging.getLogger(__name__)

FARAMIR_SELECTION_PROMPT = """\
You are Faramir, MovieWeb's box-office editorial agent. You receive a daily payload of
films currently in theatrical release and candidate comparisons — historical films
each active film has just passed (within last 3-4 days) or is about to pass (within
5%) on either domestic or worldwide totals, plus round-number milestones.

Your job is to pick the right number of comparisons (see picks_target, picks_min,
picks_max in the payload) that will become MovieWeb articles. For each pick, write:
  - A headline (declarative, news-style, max 75 chars)
  - An angle (2-3 sentences on why this matters editorially)

## Selection rules

1. Pick picks_target unless fewer non-suppressed candidates exist with richness >= 6.
   - Monday: hard target of 6. If fewer than 6 quality candidates exist, return all of them.
   - Tue-Fri: target is 4 but you have discretion to return 2, 3, or 4 based on slate
     quality. Don't manufacture picks. If only 2 strong angles exist, return 2.
   - Sat-Sun: hard target of 2.
2. Never pick a candidate where suppressed=true.
3. Max 2 picks per active film. Don't load the slate with one juggernaut.
4. Mix angle categories. Don't pick 3 franchise_siblings. Aim for variety across:
   franchise_sibling, creative_head_to_head, unexpected_giant_killer, asymmetry,
   round_number_milestone.
5. Prefer just_passed over about_to_pass UNLESS the about_to_pass candidate's
   richness_score exceeds just_passed by 5+ points.
6. When two candidates are close in richness, prefer the one with stronger MW
   talent or franchise signals (signals.is_signal=true on active or comp).
7. unexpected_giant_killer picks should feel earned — the comp film needs cultural
   weight. Best Picture winners qualify, but so do iconic blockbusters that defined
   their era regardless of awards: Harry Potter, Independence Day, Jurassic Park,
   The Lion King, Top Gun, any film that people think of as "a classic" even if it
   never won an Oscar. The test is: would a casual moviegoer recognize the comp and
   be surprised the active film passed it? A mid-tier film passing a slightly bigger
   mid-tier film isn't an unexpected_giant_killer — skip it.
8. Milestone picks ($100M / $500M / $1B etc.) are fine but cap at 1 per run unless
   it's Monday (then up to 2).
9. asymmetry picks are bonus — use them when the contrast is genuinely striking
   (worldwide 5x domestic, or vice versa, vs. cohort). Skip if the asymmetry is
   common for the genre.

## Headline guidance (MovieWeb house style)

- Lead with the news event. "X Just Passed Y" is the basic shape.
- Max 90 characters.
- Specific numbers when they're round or meaningful — "$500M", "$1 Billion", a
  rank ("4th-Biggest Horror Ever").
- Name the franchise / star / director when there's a creative through-line.
- For unexpected_giant_killer: lean into the contrast and name the iconic comp.
  "This Horror Film Just Quietly Passed Harry Potter and the Sorcerer's Stone".
  The comp's recognizability is the hook — use it.
- For milestones: declarative — "[Film] Crosses $1 Billion Worldwide", and a
  reason to read ("Becoming Only the X Movie This Year to Do So").
- No clickbait formulations: no "You Won't Believe", no "This One Stat",
  no ellipses for tease.
- Title case. No emoji.

## Angle guidance

2-3 sentences. Establish (a) what just happened or is about to happen, (b) why
it's interesting in context — the creative through-line, the genre milestone,
the unlikeliness, (c) where it leaves the active film going forward (next
landmark, comparable contemporary, franchise position).
Avoid generic studio-speak. Write like a film journalist with a strong POV.

## Output format

Return a JSON object only — no preamble, no markdown fences:
{
  "picks": [
    {
      "active_tmdb_id": 575265,
      "active_title": "Mission: Impossible \\u2013 The Final Reckoning",
      "comp_tmdb_id": 56292,
      "comp_title": "Mission: Impossible \\u2013 Ghost Protocol",
      "axis": "domestic",
      "kind": "just_passed",
      "threshold_value": null,
      "angle_category": "franchise_sibling",
      "headline": "...",
      "angle": "..."
    }
  ]
}

comp_tmdb_id and comp_title are null for milestone picks.
threshold_value is populated for milestone picks, null otherwise.\
"""


def build_payload(
    run_date: date,
    active_films_with_candidates: list[dict],
    picks_target: int,
    picks_min: int,
    picks_max: int,
) -> dict:
    """Build the JSON payload for Claude."""
    return {
        "run_date": str(run_date),
        "picks_target": picks_target,
        "picks_min": picks_min,
        "picks_max": picks_max,
        "active_films": active_films_with_candidates,
    }


def _validate_picks(picks: list[dict], payload: dict) -> list[dict]:
    """Verify every pick references a real active_tmdb_id from the payload."""
    active_ids = {f["tmdb_id"] for f in payload.get("active_films", [])}
    required_fields = {"active_tmdb_id", "active_title", "axis", "kind", "angle_category", "headline", "angle"}
    valid = []
    for pick in picks:
        if not required_fields.issubset(pick.keys()):
            logger.warning("pick missing required fields, skipping: %s", pick)
            continue
        if pick["active_tmdb_id"] not in active_ids:
            logger.warning("pick references unknown active_tmdb_id %s, skipping", pick["active_tmdb_id"])
            continue
        valid.append(pick)
    return valid


def _slim_payload(payload: dict) -> dict:
    """Trim candidate dicts to only the fields Claude needs, reducing payload size."""
    slim = {k: v for k, v in payload.items() if k != "active_films"}
    slim_films = []
    for film in payload.get("active_films", []):
        slim_candidates = []
        for c in film.get("candidates", []):
            comp = c.get("comp") or {}
            slim_candidates.append({
                "kind": c.get("kind"),
                "axis": c.get("axis"),
                "threshold_value": c.get("threshold_value"),
                "richness_score": c.get("richness_score"),
                "suppressed": c.get("suppressed", False),
                "angle_categories": c.get("angle_categories", []),
                "comp": {
                    "tmdb_id": comp.get("tmdb_id"),
                    "title": comp.get("title"),
                    "release_year": comp.get("release_year"),
                    "domestic_lifetime": comp.get("domestic_lifetime"),
                    "worldwide_lifetime": comp.get("worldwide_lifetime"),
                    "director": comp.get("director"),
                    "lead_cast": (comp.get("lead_cast") or [])[:3],
                    "genres": comp.get("genres"),
                    "collection_name": comp.get("collection_name"),
                    "is_best_picture": comp.get("is_best_picture"),
                    "is_classic": comp.get("is_classic"),
                } if comp else None,
                "signals": c.get("signals"),
            })
        slim_film = {k: v for k, v in film.items() if k != "candidates"}
        slim_film["candidates"] = slim_candidates
        slim_films.append(slim_film)
    slim["active_films"] = slim_films
    return slim


def select_picks(payload: dict) -> list[dict]:
    """Call Claude, parse response, validate picks, return list of pick dicts.

    Uses prompt caching on the system prompt (cache_control: ephemeral).
    Retries once on parse failure. Returns [] on second failure so the caller
    can post a degraded Slack message.
    """
    client = anthropic.Anthropic()
    user_message = json.dumps(_slim_payload(payload), default=str)

    def _call() -> str:
        response = client.messages.create(
            model=SELECTION_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": FARAMIR_SELECTION_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        logger.info(
            "Claude response: stop_reason=%s content_blocks=%d",
            response.stop_reason,
            len(response.content),
        )
        if not response.content:
            raise ValueError("Claude returned no content blocks")
        text = response.content[0].text
        if not text.strip():
            raise ValueError(f"Claude returned empty text (stop_reason={response.stop_reason})")
        return text

    def _parse(text: str) -> list[dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = -1 if lines[-1].strip() == "```" else len(lines)
            text = "\n".join(lines[1:end])
        data = json.loads(text)
        if isinstance(data, dict) and "picks" in data:
            return data["picks"]
        if isinstance(data, list):
            return data
        raise ValueError(f"unexpected response shape: {type(data)}")

    for attempt in range(2):
        try:
            raw = _call()
            logger.info("Claude raw response (first 300 chars): %s", raw[:300])
            picks = _parse(raw)
            picks = _validate_picks(picks, payload)
            logger.info("select_picks attempt %d: %d valid picks", attempt + 1, len(picks))
            return picks
        except Exception as exc:
            logger.warning("select_picks attempt %d failed: %s", attempt + 1, exc)

    logger.error("select_picks: both attempts failed, returning empty list")
    return []
