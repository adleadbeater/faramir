"""Increment 8: figure and claim validation, lexical scan, 85-character limit.

Unit tests build bound picks directly (the shape faramir.binding produces);
end-to-end tests run the daily pipeline on fakes. No real Anthropic, Slack or
Sheets calls.
"""

import json

import anthropic
import httpx2
import pytest

from faramir.validate import (HEADLINE_MAX, check_claims, figure_checks, length_check, lexical_checks,
                              parse_money, title_lexicon, validate_pick)

COMP = {"tmdb_id": 200, "title": "Old Classic", "genres": ["Action"], "is_best_picture": False,
        "domestic_lifetime": 145_000_000, "domestic_status": "reported",
        "worldwide_lifetime": 400_000_000, "worldwide_status": "reported", "identity_status": "verified"}
ACTIVE = {"tmdb_id": 100, "title": "New Hit", "genres": ["Action", "Adventure"],
          "domestic_today": 150_000_000, "worldwide_today": 380_000_000}


def bound(kind="just_passed", axis="domestic", comp=COMP, threshold=None, mtype=None, cats=("franchise_sibling",),
          headline="New Hit Passes Old Classic", angle="", claims=None, active=ACTIVE):
    ev = {"event": {"axis": axis, "kind": kind, "milestone_type": mtype, "threshold": threshold},
          "active": dict(active), "comp": None if comp is None else dict(comp), "categories": list(cats)}
    if claims is None:
        claims = [default_claim(kind, axis, comp, threshold, mtype)]
    return {"active_title": active["title"], "comp_title": (comp or {}).get("title"), "axis": axis, "kind": kind,
            "headline": headline, "angle": angle, "claims": claims, "evidence": ev}


def claim(type, comp_tmdb_id=None, threshold=None, axis=None, relation=None, genre=None, text="x"):
    return {"type": type, "comp_tmdb_id": comp_tmdb_id, "threshold": threshold, "axis": axis,
            "relation": relation, "genre": genre, "text": text}


def default_claim(kind, axis, comp, threshold, mtype):
    if kind == "milestone":
        return claim("crossed_milestone" if mtype == "just_passed" else "nearing_milestone", threshold=threshold, axis=axis)
    return claim("passed_comp" if kind == "just_passed" else "nearing_comp", comp_tmdb_id=(comp or {}).get("tmdb_id"), axis=axis)


NEAR = dict(ACTIVE, domestic_today=140_000_000)  # 5M short of the comp


# ── declared claims ─────────────────────────────────────────────────────

def test_each_supported_event_claim_passes():
    assert check_claims(bound()) == []
    assert check_claims(bound(kind="about_to_pass", active=NEAR)) == []
    assert check_claims(bound(kind="milestone", comp=None, threshold=100_000_000, mtype="just_passed")) == []
    assert check_claims(bound(kind="milestone", comp=None, threshold=200_000_000, mtype="about_to_pass")) == []


@pytest.mark.parametrize("wrong", [
    claim("passed_comp", comp_tmdb_id=999, axis="domestic"),              # different comp
    claim("passed_comp", comp_tmdb_id=200, axis="worldwide"),             # different axis
    claim("nearing_comp", comp_tmdb_id=200, axis="domestic"),             # wrong kind
    claim("crossed_milestone", threshold=100_000_000, axis="domestic"),   # not a milestone event
])
def test_event_claim_not_matching_the_candidate_is_rejected(wrong):
    reasons = check_claims(bound(claims=[wrong]))
    assert any(r.startswith("claim_mismatch") for r in reasons)
    assert "claims_missing_event: no declared claim states the bound event" in reasons


def test_milestone_claim_with_other_threshold_is_rejected():
    p = bound(kind="milestone", comp=None, threshold=100_000_000, mtype="just_passed",
              claims=[claim("crossed_milestone", threshold=150_000_000, axis="domestic")])
    assert any(r.startswith("claim_mismatch:crossed_milestone") for r in check_claims(p))


def test_passed_claim_contradicted_by_current_gross():
    # Bound as just_passed but today's gross is below the comp (stale/odd data).
    reasons = check_claims(bound(active=NEAR))
    assert any(r.startswith("claim_contradicted:passed_comp") for r in reasons)


def test_claim_on_unreported_comp_metric_is_rejected():
    comp = dict(COMP, domestic_lifetime=None, domestic_status="missing")
    reasons = check_claims(bound(comp=comp))
    assert any(r.startswith("claim_unavailable_metric") for r in reasons)


def test_missing_event_claim_is_rejected():
    assert check_claims(bound(claims=[])) == ["claims_missing_event: no declared claim states the bound event"]


@pytest.mark.parametrize("t", ["rank", "record", "superlative", "chart_position", "first_or_only", "count"])
def test_never_supported_claim_types(t):
    p = bound()
    p["claims"].append(claim(t, text="biggest ever"))
    (reason,) = check_claims(p)
    assert reason.startswith(f"claim_unsupported:{t}")


def test_relation_claim_needs_the_matching_candidate_category():
    ok = bound(cats=["franchise_sibling"])
    ok["claims"].append(claim("relation", relation="franchise"))
    assert check_claims(ok) == []
    for relation in ("same_director", "same_star"):
        bad = bound(cats=["franchise_sibling"])
        bad["claims"].append(claim("relation", relation=relation))
        assert any(r.startswith(f"claim_unsupported:relation:{relation}") for r in check_claims(bad))


def test_relation_claim_needs_verified_comp_identity():
    p = bound(comp=dict(COMP, identity_status="unverified"))
    p["claims"].append(claim("relation", relation="franchise"))
    assert any(r.startswith("claim_unsupported:relation") for r in check_claims(p))


def test_genre_claim_must_match_either_film():
    ok = bound()
    ok["claims"].append(claim("genre_descriptor", genre="adventure"))
    assert check_claims(ok) == []
    bad = bound()
    bad["claims"].append(claim("genre_descriptor", genre="horror"))
    assert any(r.startswith("claim_unsupported:genre:horror") for r in check_claims(bad))


# ── lexical scan (independent of declared claims) ───────────────────────

@pytest.mark.parametrize("text", [
    "New Hit Becomes the Biggest Action Movie", "Now the No. 1 Film", "Takes #1", "Enters the Top 10",
    "Sets a Record", "Highest-Grossing of the Year", "The 3rd Entry to Do It", "All-Time Great Run",
    "First Film Since 2019 to Do This", "The Only Film This Fall", "Best Run Ever", "Most Successful Entry",
    "Dethrones Old Classic",
])
def test_superlatives_and_rankings_are_rejected(text):
    p = bound(headline="New Hit Passes Old Classic", angle=text)
    assert any(r.startswith("lexical_superlative") for r in lexical_checks(p, []))


def test_words_inside_masked_titles_do_not_trigger():
    comp = dict(COMP, title="The Best Years of Our Lives")
    p = bound(comp=comp, headline="New Hit Passes The Best Years of Our Lives")
    assert lexical_checks(p, []) == []


def test_best_picture_allowed_only_for_a_best_picture_comp():
    angle = "It passes a Best Picture winner."
    assert lexical_checks(bound(comp=dict(COMP, is_best_picture=True), angle=angle), []) == []
    assert any(r.startswith("lexical_superlative") for r in lexical_checks(bound(angle=angle), []))


def test_tense_mismatch_both_directions():
    about = bound(kind="about_to_pass", active=NEAR, headline="New Hit Passes Old Classic")
    assert any(r.startswith("tense_mismatch: completed") for r in lexical_checks(about, []))
    assert lexical_checks(bound(kind="about_to_pass", active=NEAR, headline="New Hit Is About to Pass Old Classic"), []) == []
    assert any(r.startswith("tense_mismatch") for r in lexical_checks(
        bound(kind="about_to_pass", active=NEAR, headline="New Hit Beat Old Classic"), []))
    assert lexical_checks(bound(kind="about_to_pass", active=NEAR, headline="New Hit Could Beat Old Classic"), []) == []
    done = bound(headline="New Hit Is Closing In on Old Classic")
    assert any(r.startswith("tense_mismatch: anticipatory") for r in lexical_checks(done, []))
    # Milestones use the same rule via milestone_type.
    ms = bound(kind="milestone", comp=None, threshold=100_000_000, mtype="about_to_pass",
               headline="New Hit Crosses $100 Million Domestic")
    assert any(r.startswith("tense_mismatch") for r in lexical_checks(ms, []))


def test_genre_descriptor_must_belong_to_a_film():
    assert lexical_checks(bound(angle="An adventure film holding steady."), []) == []
    assert any(r.startswith("genre_unsupported") for r in lexical_checks(bound(angle="A horror movie run."), []))
    # The bare word without a film noun is not a descriptor claim.
    assert lexical_checks(bound(angle="Plenty of action at the box office."), []) == []


def test_third_film_title_is_rejected():
    lexicon = ["Star Wars", "Avatar: The Way of Water", "New Hit", "Old Classic"]
    p = bound(angle="It is now chasing Star Wars territory.")
    assert "third_film_named: 'Star Wars'" in lexical_checks(p, lexicon)
    assert lexical_checks(bound(), lexicon) == []


def test_title_lexicon_skips_short_and_unverified_titles():
    corpus = [{"title": "Up", "identity_status": "verified"}, {"title": "Titanic", "identity_status": "verified"},
              {"title": "Big Fish", "identity_status": "verified"}, {"title": "Mystery Movie", "identity_status": "unverified"}]
    assert title_lexicon(corpus, [{"title": "Smile 2"}]) == ["Big Fish", "Smile 2", "Titanic"]


# ── figures ─────────────────────────────────────────────────────────────

def test_parse_money_precision():
    (v, tol, _), = parse_money("$145.3M")
    assert v == pytest.approx(145_300_000) and tol == pytest.approx(50_000)
    (v, tol, _), = parse_money("$1 billion")
    assert v == 1e9 and tol == 0.5e9
    (v, tol, _), = parse_money("$152,000,000")
    assert v == 152_000_000 and tol == 0.5


@pytest.mark.parametrize("text, ok", [
    ("Now at $150 million domestic.", True),
    ("Now at $150.0M.", True),
    ("Past Old Classic's $145M.", True),
    ("Past Old Classic's $145.4M.", False),       # implies more precision than evidence supports
    ("Ahead by $5 million.", True),               # the gap
    ("Ahead by $6 million.", False),
    ("Old Classic made $410 million worldwide.", False),
    ("Worldwide it has $380M.", True),
])
def test_figures_must_trace_to_evidence(text, ok):
    assert (figure_checks(bound(angle=text)) == []) is ok


def test_figure_from_missing_metric_is_never_valid():
    comp = dict(COMP, worldwide_lifetime=None, worldwide_status="missing")
    assert any("unverified_figure" in r for r in figure_checks(bound(comp=comp, angle="Old Classic took $400M worldwide.")))


# ── 85-character limit ──────────────────────────────────────────────────

def test_length_limit_counts_exactly():
    assert length_check(bound(headline="x" * HEADLINE_MAX)) == []
    assert length_check(bound(headline="x" * (HEADLINE_MAX + 1))) == [f"headline_too_long: 86 > {HEADLINE_MAX} characters"]


def test_only_length_flag():
    long = "New Hit Passes Old Classic " + "x" * 80
    assert validate_pick(bound(headline=long), []).only_length
    assert not validate_pick(bound(headline=long + " biggest"), []).only_length
    assert validate_pick(bound(), []).ok


# ── shorten_headline unit ───────────────────────────────────────────────

from tests.fakes.fake_anthropic import FakeAnthropic, FakeMessage, FakeTextBlock  # noqa: E402


def _req():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_shorten_headline_success_records_usage(monkeypatch):
    from faramir import sheet
    from faramir.claude_select import shorten_headline

    monkeypatch.setattr(sheet, "_USAGE_LOG", [])
    fake = FakeAnthropic([json.dumps({"headline": "New Hit Passes Old Classic"})]).install(monkeypatch)
    headline, record = shorten_headline(bound(headline="x" * 100), HEADLINE_MAX)
    assert headline == "New Hit Passes Old Classic" and record["new_length"] == 26
    (call,) = fake.calls
    assert call["max_tokens"] == 256 and call["output_config"]["format"]["type"] == "json_schema"
    assert [u["call_label"] for u in sheet._USAGE_LOG] == ["shorten_headline"]


@pytest.mark.parametrize("item, err", [
    (FakeMessage(content=[FakeTextBlock("{}")], stop_reason="refusal"), "stop_reason refusal"),
    ("not json", "schema validation failed"),
    (anthropic.APITimeoutError(request=_req()), "APITimeoutError"),
])
def test_shorten_headline_failures_return_none(monkeypatch, item, err):
    from faramir.claude_select import shorten_headline

    FakeAnthropic([item]).install(monkeypatch)
    headline, record = shorten_headline(bound(headline="x" * 100), HEADLINE_MAX)
    assert headline is None and record["error"] == err


# ── end to end: nothing invalid reaches Slack ───────────────────────────

from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, payload_of, pick_first_candidate, pick_msgs  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402

PADDING = " as the Steady Domestic Run Rolls On Through Late October Weekdays and Into November"


def with_pick(**changes):
    def respond(kwargs):
        data = json.loads(pick_first_candidate(kwargs))
        p = data["picks"][0]
        for k, v in changes.items():
            p[k] = v(p) if callable(v) else v
        return json.dumps(data)
    return respond


def test_rank_claim_never_reaches_slack(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([with_pick(claims=lambda p: p["claims"] + [claim("rank", text="No. 1")])]).install(env["monkeypatch"])
    assert main(env) == 0
    assert pick_msgs(env["slack"]) == []
    assert "0 editorial picks" in json.dumps(header_msgs(env["slack"])[0]["blocks"])
    report, _, _ = artifacts(env)
    (entry,) = report["validation"]
    assert not entry["accepted"] and entry["reasons"][0].startswith("claim_unsupported:rank")


def test_superlative_in_angle_never_reaches_slack(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([with_pick(angle="The biggest run of the fall.")]).install(env["monkeypatch"])
    main(env)
    assert pick_msgs(env["slack"]) == []


def test_unverified_figure_never_reaches_slack(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([with_pick(angle="It has now made $987 million.")]).install(env["monkeypatch"])
    main(env)
    assert pick_msgs(env["slack"]) == []


def test_long_headline_is_shortened_once_then_posted(env):
    from faramir import sheet

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setattr(sheet, "_USAGE_LOG", [])
    shortened = {}

    def short(kwargs):
        original = json.loads(kwargs["messages"][0]["content"])["headline"]
        shortened["h"] = original.replace(PADDING, "")
        return json.dumps({"headline": shortened["h"]})

    fake = FakeAnthropic([with_pick(headline=lambda p: p["headline"] + PADDING), short]).install(env["monkeypatch"])
    assert main(env) == 0
    assert len(fake.calls) == 2
    (msg,) = pick_msgs(env["slack"])
    text = json.dumps(msg["blocks"])
    assert shortened["h"] in text and "Late October" not in text
    report, _, _ = artifacts(env)
    (entry,) = report["validation"]
    assert entry["accepted"] and entry["headline_after"] == shortened["h"]
    assert entry["reasons"][0].startswith("headline_too_long")
    assert "shorten_headline" in [u["call_label"] for u in report["claude_usage"]]


@pytest.mark.parametrize("second", [
    lambda kwargs: json.dumps({"headline": json.loads(kwargs["messages"][0]["content"])["headline"]}),  # still long
    lambda kwargs: json.dumps({"headline": "New Hit Becomes the Biggest Film"}),  # short but now invalid
    anthropic.APITimeoutError(request=_req()),
])
def test_failed_shortening_drops_the_pick_without_truncating(env, second):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([with_pick(headline=lambda p: p["headline"] + PADDING), second]).install(env["monkeypatch"])
    main(env)
    assert len(fake.calls) == 2  # exactly one shortening attempt
    assert pick_msgs(env["slack"]) == []


def test_non_length_failure_gets_no_shortening_call(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    fake = FakeAnthropic([with_pick(headline=lambda p: p["headline"] + PADDING + " Ever")]).install(env["monkeypatch"])
    main(env)
    assert len(fake.calls) == 1 and pick_msgs(env["slack"]) == []


# ── Increment 8 review: ordinary language must stay legal ───────────────
# Regression guard: these are legitimate headline/angle phrasings. If a future
# change to the lexical rules rejects one, the title engine became too strict.

LEGITIMATE = [
    "New Hit Passes Old Classic in Its First Weekend",
    "New Hit passes Old Classic in its third weekend.",
    "In its 5th week in theaters, New Hit passes Old Classic.",
    "New Hit Passes Old Classic After Only Two Weeks in Theaters",
    "Only 10 days into its run, it passed Old Classic.",
    "It passed Old Classic, not only at home.",
    "Ever since its debut, the run has held steady.",
    "Most of its gross came from domestic audiences.",
    "Its leading man passed Old Classic's star at the box office.",
    "At first the opening looked soft; it passed Old Classic anyway.",
    "Its first trailer promised a hit, and it passed Old Classic.",
    "The ever-growing run passed Old Classic.",
]


@pytest.mark.parametrize("text", LEGITIMATE)
def test_ordinary_language_is_not_a_superlative(text):
    assert lexical_checks(bound(angle=text), []) == []


def test_ordinary_phrases_pass_full_validation():
    p = bound(headline="New Hit Passes Old Classic in Its Second Weekend",
              angle="Only $5M separated them a week ago; most of its gross is domestic.")
    assert validate_pick(p, []).ok


@pytest.mark.parametrize("text", [
    "The First Film Since 2019 to Pass Old Classic",       # historical first
    "For the first time, a film passes Old Classic.",
    "The only sequel to pass Old Classic this year",       # first/only claim
    "Only two films have passed Old Classic.",              # count claim
    "Its biggest weekend ever",
    "The best opening of the fall",
    "The most successful entry in the series",
    "Now the second-biggest film of the year",              # spelled-out ordinal rank
    "The 4th Film to Pass Old Classic",
    "Leading the box office again",
    "A first-ever run past Old Classic",
])
def test_unsupported_rank_record_first_claims_stay_blocked(text):
    assert any(r.startswith("lexical_superlative") for r in lexical_checks(bound(angle=text), []))


# ── direction of the comparison ─────────────────────────────────────────

@pytest.mark.parametrize("headline, ok", [
    ("New Hit Passes Old Classic", True),
    ("Old Classic Is Passed by New Hit", True),
    ("Old Classic Falls Behind New Hit", True),
    ("Old Classic Passes New Hit", False),
    ("New Hit Is Passed by Old Classic", False),
    ("New Hit Falls Behind Old Classic", False),
])
def test_direction_of_comparison(headline, ok):
    reasons = lexical_checks(bound(headline=headline), [])
    assert (not any(r.startswith("direction_reversed") for r in reasons)) is ok


# ── genre attribution ───────────────────────────────────────────────────
# Active "New Hit" is Action/Adventure; comp "Old Classic" is Fantasy here.

FANTASY_COMP = dict(COMP, genres=["Fantasy"])


@pytest.mark.parametrize("headline, angle", [
    ("This Adventure Film Just Passed Old Classic", ""),                  # subject -> active
    ("New Hit Passes the Fantasy Classic Old Classic", ""),               # title right after -> comp
    ("New Hit Passes a Beloved Fantasy Film", ""),                         # object of the verb -> comp
    ("New Hit Passes Old Classic", "Old Classic, the fantasy hit, made $145M domestically."),  # apposition -> comp
    ("New Hit, the Action Film, Passes Old Classic", ""),                  # apposition -> active
    ("Old Classic Is Passed by an Action Movie", ""),                      # passive: agent -> active
])
def test_genre_correctly_attributed(headline, angle):
    p = bound(comp=FANTASY_COMP, headline=headline, angle=angle)
    assert [r for r in lexical_checks(p, []) if r.startswith("genre")] == []


@pytest.mark.parametrize("headline, angle", [
    ("This Fantasy Film Just Passed Old Classic", ""),                    # comp's genre on the active film
    ("New Hit Passes the Action Classic Old Classic", ""),                # active's genre on the comp
    ("New Hit Passes a Beloved Adventure Film", ""),                       # object is the comp, not adventure
    ("New Hit, the Fantasy Film, Passes Old Classic", ""),                 # apposition on the wrong film
])
def test_genre_misattributed(headline, angle):
    p = bound(comp=FANTASY_COMP, headline=headline, angle=angle)
    assert any(r.startswith("genre_misattributed") for r in lexical_checks(p, []))


def test_genre_on_neither_film_and_milestones():
    p = bound(comp=FANTASY_COMP, headline="New Hit Passes a Horror Film")
    assert any(r.startswith("genre_unsupported") for r in lexical_checks(p, []))
    ms = bound(kind="milestone", comp=None, threshold=100_000_000, mtype="just_passed",
               headline="Fantasy Film New Hit Crosses $100 Million")
    assert any(r.startswith("genre_unsupported") for r in lexical_checks(ms, []))
    ok = bound(kind="milestone", comp=None, threshold=100_000_000, mtype="just_passed",
               headline="Adventure Film New Hit Crosses $100 Million")
    assert lexical_checks(ok, []) == []


# ── shortening must not change meaning ──────────────────────────────────

from faramir.validate import meaning_changes  # noqa: E402

LONG_COMP = dict(COMP, title="Mission: Impossible – Ghost Protocol")
MS = dict(kind="milestone", comp=None, threshold=1_000_000_000, mtype="just_passed")


def test_faithful_shortenings_pass():
    p = bound(comp=LONG_COMP, headline="New Hit Officially Passes Mission: Impossible – Ghost Protocol at the Domestic Box Office")
    assert meaning_changes(p, "New Hit Passes Ghost Protocol Domestically") == []
    m = bound(**MS, headline="New Hit Officially Crosses the $1 Billion Mark at the Worldwide Box Office This Weekend")
    assert meaning_changes(m, "New Hit Crosses $1 Billion Worldwide") == []
    assert meaning_changes(m, "New Hit Crosses $1.0B Worldwide") == []


@pytest.mark.parametrize("kw, original, shortened, change", [
    ({}, "New Hit Passes Old Classic at the Box Office", "Old Classic Passes New Hit", "direction was reversed"),
    ({}, "New Hit Passes Old Classic at the Box Office", "New Hit Is Passed by Old Classic", "direction was reversed"),
    ({}, "New Hit Passes Old Classic at the Box Office", "New Hit Nears Old Classic", "status wording"),
    ({}, "New Hit Passes Old Classic at the Box Office", "New Hit Keeps Climbing", "comp film is no longer named"),
    (dict(kind="about_to_pass", active=NEAR), "New Hit Is About to Pass Old Classic", "New Hit Passes Old Classic", "status wording"),
    (MS, "New Hit Crosses $1 Billion Worldwide", "New Hit Crosses $100 Million Worldwide", "figure $1 Billion"),
    (MS, "New Hit Crosses $1 Billion Worldwide", "New Hit Crosses a Milestone", "figure $1 Billion"),
    (MS, "New Hit Crosses $1 Billion Worldwide", "New Hit Nears $1 Billion Worldwide", "status wording"),
    ({}, "New Hit Passes Old Classic", "New Hit Passes Old Classic's $145M", "was added"),
])
def test_meaning_changes_are_detected(kw, original, shortened, change):
    p = bound(headline=original, **kw)
    assert any(change in r for r in meaning_changes(p, shortened)), meaning_changes(p, shortened)


def test_meaning_changed_shortening_never_reaches_slack(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)

    def reversed_short(kwargs):
        original = json.loads(kwargs["messages"][0]["content"])["headline"]
        active, _, comp = original.replace(PADDING, "").partition(" Moves Past ")
        return json.dumps({"headline": f"{comp} Moves Past {active}"})

    fake = FakeAnthropic([with_pick(headline=lambda p: p["headline"] + PADDING), reversed_short]).install(env["monkeypatch"])
    main(env)
    assert len(fake.calls) == 2 and pick_msgs(env["slack"]) == []
    report, _, _ = artifacts(env)
    (entry,) = report["validation"]
    assert any("direction" in r for r in entry["reasons_after"]) and not entry["accepted"]
