"""Increment 7: structured Claude output, strict candidate binding, API failure
handling, retries and token limits. Offline (FakeAnthropic); no API key."""

import hashlib
import json

import anthropic
import httpx2
import pytest

from faramir import config
from faramir.binding import bind_picks, candidate_id
from faramir.claude_select import FARAMIR_SELECTION_PROMPT, SELECTION_MODEL, select
from tests.fakes.fake_anthropic import FakeAnthropic, FakeMessage, FakeTextBlock, FakeUsage

DATA_DATE = "2026-10-21"
ORIGINAL_PROMPT_SHA256 = "0cc056f0bdbb4f63ff5396d41dab3acf48903ce36beb2f3b22e8767281b0060b"  # @ f2a0ce4
# Increment 8: the approved prompt edits (85 chars, superlative examples removed, evidence rules, claims).
# Phase 2: adds the "Strategy events and headline guidance" section (nothing else changed).
PHASE2_PROMPT_SHA256 = "b0b29e69e62092e13f8b0424dbb5238135fe44d7edc1ceb68c2573bbe643b05b"
APPROVED_PROMPT_SHA256 = "775de5f2716767678cc87b80f891cd8a391b6fe82f6ba8c90c230891b2b2c4d4"


def comp(tmdb_id, title, dom, ww):
    return {"tmdb_id": tmdb_id, "imdb_id": f"tt{tmdb_id:07d}", "title": title, "release_year": "2001",
            "domestic_lifetime": dom, "domestic_status": "reported", "worldwide_lifetime": ww,
            "worldwide_status": "reported", "identity_status": "verified"}


def films():
    return [{
        "tmdb_id": 1, "title": "Alpha Film", "domestic_yesterday": 140_000_000, "domestic_today": 152_000_000,
        "worldwide_yesterday": 300_000_000, "worldwide_today": 322_000_000, "days_in_release": 33,
        "candidates": [
            {"kind": "just_passed", "axis": "domestic", "threshold_value": 150_000_000, "richness_score": 13,
             "suppressed": False, "angle_categories": ["franchise_sibling"], "comp": comp(9, "Nine", 150_000_000, 400_000_000)},
            {"kind": "about_to_pass", "axis": "domestic", "threshold_value": 155_000_000, "richness_score": 9,
             "suppressed": False, "angle_categories": ["creative_head_to_head_director"], "comp": comp(10, "Ten", 155_000_000, 310_000_000)},
            {"kind": "milestone", "axis": "domestic", "threshold_value": 150_000_000, "milestone_type": "just_passed",
             "richness_score": 9, "suppressed": False, "angle_categories": ["round_number_milestone"], "comp": None},
            {"kind": "milestone", "axis": "worldwide", "threshold_value": 300_000_000, "milestone_type": "just_passed",
             "richness_score": 9, "suppressed": True, "angle_categories": ["round_number_milestone"], "comp": None},
        ],
    }]


def pick(**kw):
    p = {"active_tmdb_id": 1, "active_title": "Alpha Film", "comp_tmdb_id": 9, "comp_title": "Nine",
         "axis": "domestic", "kind": "just_passed", "threshold_value": None,
         "angle_category": "franchise_sibling", "headline": "Alpha Film Passes Nine", "angle": "a",
         "claims": [{"type": "passed_comp", "comp_tmdb_id": 9, "threshold": None, "axis": "domestic",
                     "relation": None, "genre": None, "text": "passes Nine"}]}
    p.update(kw)
    return p


def bind(*picks):
    return bind_picks(list(picks), films(), DATA_DATE, "2026-10-01")


# ── binding: accepted ───────────────────────────────────────────────────

def test_valid_comparison_pick_is_bound_with_evidence():
    r = bind(pick())
    (a,) = r.accepted
    assert a["candidate_id"] == candidate_id(DATA_DATE, 1, 9, None, "domestic", "just_passed")
    ev = a["evidence"]
    assert ev["comp"]["domestic_lifetime"] == 150_000_000 and ev["comp"]["domestic_status"] == "reported"
    assert ev["active"]["domestic_today"] == 152_000_000 and ev["data_date"] == DATA_DATE
    assert ev["corpus_gross_as_of"] == "2026-10-01"
    assert a["evidence_ref"] == hashlib.sha1(json.dumps(ev, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    assert bind(pick()).accepted[0]["candidate_id"] == a["candidate_id"]  # stable


def test_valid_milestone_and_category_family():
    r = bind(pick(comp_tmdb_id=None, comp_title=None, kind="milestone", threshold_value=150_000_000,
                  angle_category="round_number_milestone"),
             pick(comp_tmdb_id=10, comp_title="Ten", kind="about_to_pass", angle_category="creative_head_to_head"))
    assert len(r.accepted) == 2 and r.rejected == []


def test_matching_threshold_is_accepted():
    assert bind(pick(threshold_value=150_000_000)).accepted


# ── binding: rejected, each with a reason ───────────────────────────────

@pytest.mark.parametrize("bad, reason", [
    (dict(comp_tmdb_id=12345), "invented comparison"),
    (dict(comp_tmdb_id=10, kind="about_to_pass", axis="worldwide", angle_category="creative_head_to_head"), "wrong axis"),
    (dict(kind="about_to_pass"), "does not match the candidate's 'just_passed'"),
    (dict(threshold_value=999), "does not match the comparison"),
    (dict(kind="milestone"), "kind 'milestone' given with a comparison film"),
    (dict(comp_tmdb_id=None, kind="just_passed"), "requires a comparison film"),
    (dict(comp_tmdb_id=None, kind="milestone", threshold_value=None), "milestone without a threshold"),
    (dict(comp_tmdb_id=None, kind="milestone", threshold_value=200_000_000, angle_category="round_number_milestone"),
     "unsupported milestone"),
    (dict(comp_tmdb_id=None, kind="milestone", axis="worldwide", threshold_value=150_000_000,
          angle_category="round_number_milestone"), "wrong axis"),
    (dict(comp_tmdb_id=None, kind="milestone", axis="worldwide", threshold_value=300_000_000,
          angle_category="round_number_milestone"), "suppressed"),
    (dict(angle_category="unexpected_giant_killer"), "not supported by the candidate"),
    (dict(angle_category="asymmetry"), "not supported by the candidate"),
    (dict(active_tmdb_id=777), "not in today's payload"),
])
def test_rejections(bad, reason):
    r = bind(pick(**bad))
    assert r.accepted == [] and reason in r.rejected[0]["reason"]


def test_duplicate_pick_rejected():
    r = bind(pick(), pick(headline="Same candidate again"))
    assert len(r.accepted) == 1 and "duplicate" in r.rejected[0]["reason"]


def test_ambiguous_match_rejected():
    fs = films()
    fs[0]["candidates"].append(dict(fs[0]["candidates"][0]))  # the same candidate twice
    r = bind_picks([pick()], fs, DATA_DATE)
    assert r.accepted == [] and "ambiguous" in r.rejected[0]["reason"]


# ── structured output and failure handling ──────────────────────────────

def payload():
    return {"run_date": "2026-10-22", "picks_target": 4, "picks_min": 2, "picks_max": 4, "active_films": films()}


def ok_text(*picks):
    return json.dumps({"picks": list(picks or [pick()])})


def msg(text, stop="end_turn"):
    return FakeMessage(content=[FakeTextBlock(text)], stop_reason=stop)


def test_request_uses_schema_model_limits_and_unchanged_prompt(monkeypatch):
    fake = FakeAnthropic([ok_text()]).install(monkeypatch)
    res = select(payload())
    assert res.status == "ok" and len(res.picks) == 1
    call = fake.calls[0]
    assert call["model"] == SELECTION_MODEL == "claude-sonnet-4-6"
    assert call["max_tokens"] == config.SELECTION_MAX_TOKENS
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["required"] == ["picks"]
    assert hashlib.sha256(FARAMIR_SELECTION_PROMPT.encode()).hexdigest() == PHASE2_PROMPT_SHA256
    assert call["system"][0]["text"] == FARAMIR_SELECTION_PROMPT
    assert fake.client_kwargs == [{"timeout": config.SELECTION_TIMEOUT_S, "max_retries": config.SELECTION_MAX_RETRIES}]


def test_refusal_is_not_retried_and_yields_no_picks(monkeypatch):
    fake = FakeAnthropic([msg("I can't help with that.", stop="refusal")]).install(monkeypatch)
    res = select(payload())
    assert (res.status, res.reason, res.picks, len(fake.calls)) == ("failed", "refusal", [], 1)


def test_truncation_yields_no_picks(monkeypatch):
    fake = FakeAnthropic([msg('{"picks": [{"active_tmdb_id": 1, "acti', stop="max_tokens")]).install(monkeypatch)
    res = select(payload())
    assert (res.status, res.reason, res.picks, len(fake.calls)) == ("failed", "truncated", [], 1)


def test_malformed_then_valid_retries_once(monkeypatch):
    from faramir import sheet

    sheet._USAGE_LOG.clear()
    fake = FakeAnthropic(["not json", ok_text()]).install(monkeypatch)
    res = select(payload())
    assert res.status == "ok" and len(fake.calls) == 2
    assert [a.get("error", "ok")[:6] for a in res.attempts] == ["schema", "ok"]
    assert [u["call_label"] for u in sheet._USAGE_LOG] == ["select_picks#1", "select_picks#2"]  # every attempt costed
    sheet._USAGE_LOG.clear()


@pytest.mark.parametrize("bad", [
    "not json",
    json.dumps({"picks": [dict(pick(), axis="global")]}),               # enum violation
    json.dumps({"picks": [dict(pick(), extra_field=1)]}),               # extra field
    json.dumps({"picks": [{k: v for k, v in pick().items() if k != "headline"}]}),  # missing field
])
def test_schema_invalid_output_is_never_used(monkeypatch, bad):
    FakeAnthropic([bad, bad]).install(monkeypatch)
    res = select(payload())
    assert (res.status, res.reason, res.picks) == ("failed", "malformed_output", [])


def _req():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("exc, reason", [
    (lambda: anthropic.APITimeoutError(request=_req()), "api_timeout"),
    (lambda: anthropic.APIConnectionError(request=_req()), "api_connection_error"),
    (lambda: anthropic.InternalServerError("boom", response=httpx2.Response(500, request=_req()), body=None), "api_status_500"),
    (lambda: anthropic.BadRequestError("bad", response=httpx2.Response(400, request=_req()), body=None), "api_status_400"),
])
def test_api_failures_fail_safe_without_extra_attempts(monkeypatch, exc, reason):
    fake = FakeAnthropic([exc()]).install(monkeypatch)
    res = select(payload())
    assert (res.status, res.reason, res.picks, len(fake.calls)) == ("failed", reason, [], 1)


# ── cost controls ───────────────────────────────────────────────────────

def test_oversized_payload_is_trimmed_by_richness(monkeypatch):
    monkeypatch.setattr(config, "SELECTION_MAX_INPUT_TOKENS", 50_000)
    fake = FakeAnthropic([ok_text()], token_counts=[100_000, 40_000]).install(monkeypatch)
    res = select(payload())
    assert res.status == "ok" and res.candidates_trimmed > 0
    sent = json.loads(fake.calls[0]["messages"][0]["content"])
    kept = sent["active_films"][0]["candidates"]
    assert kept and kept[0]["richness_score"] == 13  # the richest candidate survives


def test_payload_still_too_large_is_skipped_without_a_call(monkeypatch):
    monkeypatch.setattr(config, "SELECTION_MAX_INPUT_TOKENS", 50_000)
    fake = FakeAnthropic([ok_text()], token_counts=[100_000, 90_000]).install(monkeypatch)
    res = select(payload())
    assert res.status == "skipped_token_cap" and fake.calls == []


def test_token_count_failure_falls_back_to_estimate(monkeypatch):
    fake = FakeAnthropic([ok_text()], token_counts=[RuntimeError("count down")]).install(monkeypatch)
    res = select(payload())
    assert res.status == "ok" and res.input_tokens_counted > 0 and len(fake.calls) == 1


# ── full pipeline ───────────────────────────────────────────────────────

from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, pick_first_candidate, pick_msgs  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_api_failure_posts_no_picks_and_warns_in_header(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([anthropic.APITimeoutError(request=_req())]).install(env["monkeypatch"])
    assert main(env) == 0
    assert pick_msgs(env["slack"]) == []
    (hdr,) = header_msgs(env["slack"])
    assert "Pick selection unavailable today (api_timeout)" in json.dumps(hdr["blocks"], ensure_ascii=False)
    assert not [c for c in env["gc"].writes() if c[0] == "suggestions" and c[1] == "append_rows"]
    report, _, _ = artifacts(env)
    assert report["claude_selection"]["reason"] == "api_timeout"


def test_retry_after_malformed_output_posts_exactly_once(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic(["{oops", pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    assert len(pick_msgs(env["slack"])) == 1


def test_accepted_pick_row_carries_candidate_and_evidence(env):
    from faramir.suggestions import read_table

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    (row,) = read_table(env["gc"], "S").rows
    ev = json.loads(row.get("evidence_json"))
    assert len(row.get("candidate_id")) == 16 and ev["active"]["domestic_today"] == 152_000_000
    assert ev["comp"]["identity_status"] == "verified" and ev["corpus_gross_as_of"] == "2026-10-01"
    report, _, _ = artifacts(env)
    assert report["claude_binding"]["accepted"][0]["candidate_id"] == row.get("candidate_id")
