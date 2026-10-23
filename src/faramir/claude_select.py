"""Anthropic API pick selection for Faramir."""

import json
import logging
from dataclasses import dataclass, field
from datetime import date

import anthropic
import pydantic

from faramir import config
from faramir.config import SELECTION_MODEL, SELECTION_MAX_TOKENS
from faramir.schemas import PickSet, output_format
from faramir.sheet import buffer_usage

logger = logging.getLogger(__name__)

FARAMIR_SELECTION_PROMPT = """\
You are Faramir, MovieWeb's box-office editorial agent. You receive a daily payload of
films currently in theatrical release and candidate comparisons — historical films
each active film has just passed (within last 3-4 days) or is about to pass (within
5%) on either domestic or worldwide totals, plus round-number milestones.

Your job is to pick the right number of comparisons (see picks_target, picks_min,
picks_max in the payload) that will become MovieWeb articles. For each pick, write:
  - A headline (declarative, news-style, max 85 characters)
  - An angle (2-3 sentences on why this matters editorially)

## Selection rules

1. picks_target/picks_min/picks_max define a ceiling and a floor, not a quota to
   hit. Rank all non-suppressed candidates across the ENTIRE slate (all active
   films together, not per-film) by how good the story is, and fill from the top
   down to picks_max. Stop early — anywhere between picks_min and picks_max — the
   moment quality drops off. Never pad with a weak candidate just to reach
   picks_target. picks_min is a floor only if that many genuinely earn a slot;
   it is not a guarantee.
   - Monday: ceiling of 6.
   - Tue-Fri: ceiling of 4.
   - Sat-Sun: ceiling of 2.
2. Never pick a candidate where suppressed=true.
3. Per-film cap is a guideline, not a hard rule: a typical film should get at most
   2 picks so one juggernaut doesn't eat the slate. An active film flagged
   is_signal=true (a Cerebro-confirmed high-traffic director, star, or franchise —
   i.e. a story that matters more to us commercially) can exceed 2 picks IF there
   are multiple genuinely distinct, strong angles for it. Use judgment — "it
   matters to us" is not itself a reason to add a weak third pick. Don't overdo it.
4. Mix angle categories. Don't pick 3 franchise_siblings. Aim for variety across:
   franchise_sibling, creative_head_to_head, unexpected_giant_killer, asymmetry,
   round_number_milestone. Creative head-to-head angles (a director or star
   passing their own prior work) are rarer and more valuable than
   unexpected_giant_killer surprises — when an unexpected_giant_killer candidate
   and a creative_head_to_head candidate have comparable richness, prefer the
   creative_head_to_head one. Unexpected_giant_killer should not dominate the
   slate; if most of your strongest candidates on a given day are
   unexpected_giant_killer, that's a signal to look harder for the rarer angles
   before settling.
5. Prefer just_passed over about_to_pass UNLESS the about_to_pass candidate's
   richness_score exceeds just_passed by 5+ points.
6. When two candidates are close in richness, prefer the one with stronger MW
   talent or franchise signals (signals.is_signal=true on active or comp).
7. A story on an is_signal=true active film should rank at the TOP of the
   day's slate by priority, not just win ties — don't let a Tier-1 story (e.g.
   "closing on an all-time domestic record") get buried as one line among several
   lesser picks. Give it prominence commensurate with how big the story actually is.
8. unexpected_giant_killer picks should feel earned — the comp film needs cultural
   weight. Best Picture winners qualify, but so do iconic blockbusters that defined
   their era regardless of awards: Harry Potter, Independence Day, Jurassic Park,
   The Lion King, Top Gun, any film that people think of as "a classic" even if it
   never won an Oscar. The test is: would a casual moviegoer recognize the comp and
   be surprised the active film passed it? A mid-tier film passing a slightly bigger
   mid-tier film isn't an unexpected_giant_killer — skip it.
   - Plausibility check: eligibility and all reported figures use RAW, unadjusted
     grosses — do not inflation-adjust the numbers themselves. But before finalizing
     an unexpected_giant_killer or milestone pick against an older comp (pre-1990s
     especially), mentally sanity-check it against inflation. If the comparison
     would look absurd or embarrassing once you account for how much bigger the
     comp actually was in real terms at the time (e.g. a modest current release
     "beating" a comp that was actually a much bigger cultural and commercial event
     once inflation is considered), DO NOT run the pick — skip it entirely, don't
     just caveat it.
9. Milestone picks ($100M / $500M / $1B etc.) are fine but cap at 1 per run unless
   it's Monday (then up to 2). Critically: if you have a strong unexpected_giant_killer
   candidate (iconic comp, high richness), prefer it over a $100M–$300M milestone. Only
   choose the milestone over a compelling giant-killer if the milestone is $500M+.
10. asymmetry picks are bonus — use them when the contrast is genuinely striking
    (worldwide 5x domestic, or vice versa, vs. cohort). Skip if the asymmetry is
    common for the genre.

## Headline guidance (MovieWeb house style)

- Lead with the news event, but do NOT default to "X Is About to Pass/Surpass Y"
  as your only construction. That shape is fine occasionally, but if most of a
  day's headlines use it, that's a failure of variety — vary the construction
  pick to pick. Alternatives to rotate through:
  - Lead with the number: "$500M and Climbing: [Film] Nears a Major Milestone"
  - Lead with the franchise/talent angle: "[Director]'s Latest Just Outdid Their
    Own [Prior Film]"
  - Lead with the contrast for giant-killer stories (see below)
  - Frame as an ongoing story rather than a single crossing: "[Film]'s Box Office
    Run Keeps Getting Bigger"
  Treat "X beats/passes/surpasses Y" as one tool among several, not the default.
- Max 85 characters.
- Specific numbers when they're round or meaningful — "$500M", "$1 Billion".
- Name the franchise / star / director when there's a creative through-line.
- For unexpected_giant_killer: lean into the contrast and name the iconic comp.
  "This Horror Film Just Quietly Passed Harry Potter and the Sorcerer's Stone".
  The comp's recognizability is the hook — use it.
- For milestones: declarative — "[Film] Crosses $1 Billion Worldwide".
- No clickbait formulations: no "You Won't Believe", no "This One Stat",
  no ellipses for tease.
- Title case. No emoji.

## Angle guidance

1-2 sentences. Punchy, specific, film-journalist POV. One sentence on what just
happened and why it's surprising or meaningful; one sentence on what comes next
or what it says about the film's run. No filler, no hedging, no generic "this is
a significant achievement." If you can't say something interesting in 2 sentences,
the pick probably isn't strong enough.

## Evidence rules (enforced in code — a pick that breaks one is dropped)

- angle_category must be one of the chosen candidate's angle_categories
  ("creative_head_to_head" may stand for creative_head_to_head_director or
  creative_head_to_head_cast).
- Only assert facts present in the candidate: the comparison or milestone it
  passed or is approaching, the figures in the payload, and the relationships
  its angle_categories show (franchise, same director, same star). A null value
  is unavailable — never cite or estimate it.
- No rankings, records or superlatives ("No. 1", "4th-biggest", "record",
  "biggest ever", "highest-grossing", "first", "only"): nothing in the payload
  supports them.
- Every dollar figure must be one of the payload's figures, or the gap to the
  comparison/threshold, at the precision you write it ("$1.67B", "$152M").
- Completed wording ("passes", "crosses", "tops") only for just_passed and
  milestones already crossed; anticipatory wording ("nears", "closing in on")
  for about_to_pass.
- List every factual assertion in the headline and angle in "claims".

## Output format

Return a JSON object ONLY. Do not write any analysis, reasoning, or prose before or after the JSON.
Your entire response must be valid JSON starting with { and ending with }. No markdown fences:
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
      "angle": "...",
      "claims": [
        {"type": "passed_comp", "comp_tmdb_id": 56292, "threshold": null, "axis": "domestic",
         "relation": null, "genre": null, "text": "passes Ghost Protocol domestically"},
        {"type": "relation", "comp_tmdb_id": 56292, "threshold": null, "axis": null,
         "relation": "franchise", "genre": null, "text": "the franchise's earlier entry"}
      ]
    }
  ]
}

comp_tmdb_id and comp_title are null for milestone picks.
threshold_value is populated for milestone picks, null otherwise.
Claim types: passed_comp, nearing_comp, crossed_milestone, nearing_milestone,
relation (franchise | same_director | same_star), genre_descriptor (genre named),
and — if you make one despite the rules — rank, record, superlative,
chart_position, first_or_only, count.\
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


@dataclass
class SelectionResult:
    """Outcome of the selection call. ``picks`` is empty unless status == "ok"."""

    status: str  # ok | failed | skipped_token_cap
    picks: list[dict] = field(default_factory=list)
    reason: str = ""
    attempts: list[dict] = field(default_factory=list)
    input_tokens_counted: int | None = None
    candidates_trimmed: int = 0

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "picks"} | {"picks_returned": len(self.picks)}


def _system() -> list[dict]:
    return [{"type": "text", "text": FARAMIR_SELECTION_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _count_tokens(client, slim: dict, fmt: dict) -> tuple[int, str]:
    """(tokens, method). Falls back to a conservative local estimate if counting fails."""
    try:
        res = client.messages.count_tokens(
            model=SELECTION_MODEL, system=_system(), output_config={"format": fmt},
            messages=[{"role": "user", "content": json.dumps(slim, default=str)}],
        )
        return int(res.input_tokens), "count_tokens"
    except Exception as exc:  # counting must never block a run; over-estimate instead
        logger.warning("count_tokens failed (%s); using a local estimate", type(exc).__name__)
        chars = len(FARAMIR_SELECTION_PROMPT) + len(json.dumps(slim, default=str))
        return chars // 3 + 1, "estimate"


def _trim(slim: dict, keep_fraction: float) -> tuple[dict, int]:
    """Keep the highest-richness candidates (globally) so the payload shrinks to keep_fraction."""
    cands = [(c.get("richness_score") or 0, fi, ci) for fi, f in enumerate(slim["active_films"])
             for ci, c in enumerate(f["candidates"])]
    keep_n = max(0, int(len(cands) * keep_fraction))
    keep = {(fi, ci) for _, fi, ci in sorted(cands, key=lambda t: -t[0])[:keep_n]}
    trimmed = dict(slim, active_films=[
        dict(f, candidates=[c for ci, c in enumerate(f["candidates"]) if (fi, ci) in keep])
        for fi, f in enumerate(slim["active_films"])])
    return trimmed, len(cands) - keep_n


def select(payload: dict) -> SelectionResult:
    """Ask Claude for picks with schema-enforced output. Fails safe: any
    refusal, truncation, timeout, API error or unparseable output yields no
    picks (never a guess, never a stale or reused recommendation)."""
    client = anthropic.Anthropic(timeout=config.SELECTION_TIMEOUT_S, max_retries=config.SELECTION_MAX_RETRIES)
    fmt = output_format()
    slim = _slim_payload(payload)
    result = SelectionResult(status="failed")

    tokens, method = _count_tokens(client, slim, fmt)
    result.input_tokens_counted = tokens
    cap = config.SELECTION_MAX_INPUT_TOKENS
    if tokens > cap:
        slim, result.candidates_trimmed = _trim(slim, max(0.0, (cap * 0.9) / tokens))
        tokens, method = _count_tokens(client, slim, fmt)
        result.input_tokens_counted = tokens
        logger.warning("Selection payload trimmed by %d candidate(s) to fit %d input tokens", result.candidates_trimmed, cap)
        if tokens > cap or not any(f["candidates"] for f in slim["active_films"]):
            result.status, result.reason = "skipped_token_cap", f"payload {tokens} tokens > cap {cap} after trimming"
            return result
    user_message = json.dumps(slim, default=str)

    for attempt in range(1, config.SELECTION_ATTEMPTS + 1):
        record: dict = {"attempt": attempt}
        result.attempts.append(record)
        try:
            response = client.messages.create(
                model=SELECTION_MODEL,
                max_tokens=SELECTION_MAX_TOKENS,
                system=_system(),
                messages=[{"role": "user", "content": user_message}],
                output_config={"format": fmt},
            )
        except anthropic.APITimeoutError as exc:
            record["error"] = f"timeout after SDK retries: {exc}"
            result.reason = "api_timeout"
            return result
        except anthropic.APIConnectionError as exc:
            record["error"] = f"connection error after SDK retries: {exc}"
            result.reason = "api_connection_error"
            return result
        except anthropic.APIStatusError as exc:
            record["error"] = f"HTTP {exc.status_code}: {str(exc)[:300]}"
            result.reason = f"api_status_{exc.status_code}"
            return result

        u = response.usage
        record.update(stop_reason=response.stop_reason, input_tokens=u.input_tokens, output_tokens=u.output_tokens)
        buffer_usage(
            call_label=f"select_picks#{attempt}",
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_created=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            model=SELECTION_MODEL,
        )
        logger.info("Claude selection attempt %d: stop_reason=%s in=%s out=%s",
                    attempt, response.stop_reason, u.input_tokens, u.output_tokens)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            record["error"] = f"refusal ({getattr(details, 'category', None)})"
            result.reason = "refusal"
            return result
        if response.stop_reason == "max_tokens":
            record["error"] = f"truncated at max_tokens={SELECTION_MAX_TOKENS}"
            result.reason = "truncated"
            return result
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        try:
            parsed = PickSet.model_validate_json(text)
        except pydantic.ValidationError as exc:
            record["error"] = f"schema validation failed: {exc.error_count()} error(s)"
            result.reason = "malformed_output"
            continue  # one more attempt; output that fails the schema is never used
        result.status, result.reason = "ok", ""
        result.picks = [p.model_dump() for p in parsed.picks]
        return result
    return result


def select_picks(payload: dict) -> list[dict]:
    """Back-compatible wrapper: picks on success, [] on any failure."""
    return select(payload).picks


HEADLINE_REWRITE_PROMPT = """\
You shorten MovieWeb headlines. Rewrite the given headline to at most {max_chars} characters.
Keep exactly the same facts, the same named films and the same tense ("passes" stays completed,
"nears" stays anticipatory). Do not add figures, rankings, records or superlatives. Title case,
no emoji, no clickbait. Return JSON only: {{"headline": "..."}}"""


def shorten_headline(pick: dict, max_chars: int) -> tuple[str | None, dict]:
    """One structured call to shorten an over-length headline. (headline|None, record).

    The result is re-validated by the caller; it is never truncated here.
    """
    from faramir.schemas import Headline, headline_format

    record: dict = {"call": "shorten_headline", "original_length": len(pick.get("headline") or "")}
    client = anthropic.Anthropic(timeout=config.SELECTION_TIMEOUT_S, max_retries=config.SELECTION_MAX_RETRIES)
    try:
        response = client.messages.create(
            model=SELECTION_MODEL,
            max_tokens=256,
            system=HEADLINE_REWRITE_PROMPT.format(max_chars=max_chars),
            messages=[{"role": "user", "content": json.dumps(
                {"headline": pick.get("headline"), "angle": pick.get("angle"), "max_chars": max_chars})}],
            output_config={"format": headline_format()},
        )
    except anthropic.APIError as exc:
        record["error"] = f"{type(exc).__name__}"
        return None, record
    u = response.usage
    buffer_usage(call_label="shorten_headline", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                 cache_created=getattr(u, "cache_creation_input_tokens", 0) or 0,
                 cache_read=getattr(u, "cache_read_input_tokens", 0) or 0, model=SELECTION_MODEL)
    record["stop_reason"] = response.stop_reason
    if response.stop_reason != "end_turn":
        record["error"] = f"stop_reason {response.stop_reason}"
        return None, record
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    try:
        headline = Headline.model_validate_json(text).headline.strip()
    except pydantic.ValidationError:
        record["error"] = "schema validation failed"
        return None, record
    record["new_length"] = len(headline)
    return headline, record
