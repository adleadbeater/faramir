"""Increment 2: missing grosses are never treated as zero, and existing
matcher/milestone/validation logic works unchanged on schema-v2 records."""

import importlib.util
import itertools
import json
import os

import pytest

from faramir.corpus import build_index, reported, top_n_by_genre
from faramir.matcher import detect_angle_categories, detect_asymmetry, find_all_candidates, find_candidates
from faramir.milestone import find_milestones
from tests.test_matcher import make_corpus_film, make_film

ROOT = os.path.join(os.path.dirname(__file__), "..")


def to_v2(f):
    """Convert a v1-style fixture row to schema v2 (0 → None/missing)."""
    g = dict(f, identity_status="verified")
    for m in ("domestic", "worldwide"):
        v = g.get(f"{m}_lifetime")
        g[f"{m}_lifetime"] = v if v else None
        g[f"{m}_status"] = "reported" if v else "missing"
    return g


ACTIVE = make_film(domestic_today=150_000_000, domestic_yesterday=140_000_000,
                   worldwide_today=350_000_000, worldwide_yesterday=330_000_000)
DOM_PASSED = 145_000_000   # just_passed on domestic
WW_PASSED = 340_000_000    # just_passed on worldwide


# ── every reported/missing combination × each axis ──────────────────────

@pytest.mark.parametrize("dom_reported, ww_reported", list(itertools.product([True, False], repeat=2)))
def test_candidates_for_every_missing_combination(dom_reported, ww_reported):
    comp = to_v2(make_corpus_film(
        tmdb_id=500,
        domestic_lifetime=DOM_PASSED if dom_reported else 0,
        worldwide_lifetime=WW_PASSED if ww_reported else 0,
    ))
    cands = find_candidates(ACTIVE, build_index([comp]), [comp])
    axes = {c["axis"] for c in cands}
    assert ("domestic" in axes) == dom_reported
    assert ("worldwide" in axes) == ww_reported
    for c in cands:
        assert c["threshold_value"] == reported(comp, c["axis"])  # never a missing/zero value


def test_missing_domestic_film_stays_eligible_for_worldwide():
    comp = to_v2(make_corpus_film(tmdb_id=501, domestic_lifetime=0, worldwide_lifetime=WW_PASSED))
    (cand,) = find_candidates(ACTIVE, build_index([comp]), [comp])
    assert (cand["axis"], cand["kind"], cand["threshold_value"]) == ("worldwide", "just_passed", WW_PASSED)


def test_missing_worldwide_comp_is_not_notable_and_has_no_genre_rank():
    comp = to_v2(make_corpus_film(tmdb_id=502, genres=["Action"], domestic_lifetime=DOM_PASSED, worldwide_lifetime=0))
    cats = detect_angle_categories(ACTIVE, comp, [comp])
    assert "unexpected_giant_killer" not in cats
    assert "genre_milestone" not in cats


def test_genre_rank_ignores_records_with_missing_worldwide():
    ranked = [to_v2(make_corpus_film(tmdb_id=600 + i, genres=["Action"], worldwide_lifetime=(i + 1) * 100_000_000)) for i in range(5)]
    unranked = to_v2(make_corpus_film(tmdb_id=699, genres=["Action"], worldwide_lifetime=0))
    corpus = ranked + [unranked]
    assert [f["tmdb_id"] for f in top_n_by_genre(corpus, "Action", n=10)] == [604, 603, 602, 601, 600]
    assert "genre_milestone" in detect_angle_categories(ACTIVE, ranked[0], corpus)


def test_asymmetry_cohort_skips_missing():
    cohort = [to_v2(make_corpus_film(tmdb_id=700 + i, genres=["Action"], release_year="2023",
                                     domestic_lifetime=100_000_000, worldwide_lifetime=250_000_000)) for i in range(5)]
    gaps = [to_v2(make_corpus_film(tmdb_id=800 + i, genres=["Action"], release_year="2023",
                                   domestic_lifetime=0, worldwide_lifetime=250_000_000)) for i in range(10)]
    active = dict(ACTIVE, worldwide_today=600_000_000)  # ratio 4.0 vs cohort 2.5
    result = detect_asymmetry(active, cohort + gaps)
    assert result and result["cohort_median"] == pytest.approx(2.5)
    assert detect_asymmetry(active, gaps) is None  # cohort of only-missing rows is empty


def test_milestones_do_not_read_missing_worldwide():
    # An active film with no worldwide figure yet (0 from an unscraped page)
    # produces domestic milestones only.
    active = dict(ACTIVE, domestic_today=101_000_000, domestic_yesterday=98_000_000, worldwide_today=0, worldwide_yesterday=0)
    ms = find_milestones(active)
    assert ms and {m["axis"] for m in ms} == {"domestic"}


# ── existing matcher scenarios: identical results on v1 and v2 rows ─────

SCENARIOS = [
    dict(tmdb_id=10, domestic_lifetime=145_000_000, worldwide_lifetime=600_000_000),
    dict(tmdb_id=11, domestic_lifetime=155_000_000, worldwide_lifetime=360_000_000),
    dict(tmdb_id=12, domestic_lifetime=0, worldwide_lifetime=340_000_000),
    dict(tmdb_id=13, collection_id=42, domestic_lifetime=146_000_000, worldwide_lifetime=0),
    dict(tmdb_id=14, director="Jane Smith", domestic_lifetime=149_000_000, worldwide_lifetime=345_000_000, is_best_picture=True),
    dict(tmdb_id=15, lead_cast=["Actor A"], domestic_lifetime=152_000_000, worldwide_lifetime=900_000_000, is_classic=True),
]


def _key(cands):
    return sorted((c["comp_tmdb_id"], c["axis"], c["kind"], tuple(sorted(c["angle_categories"])), c["richness_score"]) for c in cands)


def test_find_all_candidates_identical_on_v1_and_v2_rows():
    v1 = [make_corpus_film(**s) for s in SCENARIOS]
    v2 = [to_v2(f) for f in v1]
    active = dict(ACTIVE, collection_id=42)
    suppressed = {(1, 11, "domestic")}
    r1 = find_all_candidates([active], v1, {}, suppressed)
    r2 = find_all_candidates([active], v2, {}, suppressed)
    assert r1 and _key(r1) == _key(r2)


# ── interim figure guard in run_daily ───────────────────────────────────

def _run_daily():
    spec = importlib.util.spec_from_file_location("run_daily", os.path.join(ROOT, "scripts", "run_daily.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = _run_daily()._validate_pick_figures
COMP_FULL = to_v2(make_corpus_film(domestic_lifetime=145_000_000, worldwide_lifetime=600_000_000))
COMP_NO_DOM = to_v2(make_corpus_film(domestic_lifetime=0, worldwide_lifetime=340_000_000))


def pick(axis="worldwide", headline="", angle="", kind="just_passed", **kw):
    return dict(axis=axis, kind=kind, headline=headline, angle=angle, **kw)


def test_axis_with_missing_comp_gross_is_dropped():
    assert "domestic gross unavailable" in V(pick(axis="domestic"), COMP_NO_DOM, ACTIVE)


def test_worldwide_pick_with_missing_domestic_and_traceable_figures_passes():
    p = pick(headline="Film Passes Classic Worldwide", angle="Now at $350M worldwide, past Classic's $340M.")
    assert V(p, COMP_NO_DOM, ACTIVE) is None


def test_untraceable_figure_dropped_when_a_comp_gross_is_missing():
    # $210M could only be the comp's (unavailable) domestic gross from memory.
    p = pick(angle="Classic made $210M domestically and $340M worldwide.")
    reason = V(p, COMP_NO_DOM, ACTIVE)
    assert reason and "matches no reported figure" in reason and "domestic gross unavailable" in reason


def test_existing_checks_unchanged_when_both_grosses_reported():
    assert V(pick(angle="Now past $600M"), COMP_FULL, ACTIVE) is None
    assert "block/prose disagreement" in V(pick(angle="Classic made $2 billion"), COMP_FULL, ACTIVE)
    bad = to_v2(make_corpus_film(domestic_lifetime=300_000_000, worldwide_lifetime=200_000_000))
    assert "partial-run" in V(pick(), bad, ACTIVE)


def test_milestone_pick_without_comp_is_untouched():
    assert V(pick(kind="milestone", angle="$1 billion"), None, ACTIVE) is None


# ── Slack shows unavailable values as n/a, not zero or a dash ───────────

def test_slack_block_renders_missing_comp_gross_as_na(monkeypatch):
    from faramir.slack_post import post_pick
    from tests.fakes.fake_http import FakeHTTP
    from tests.fakes.fake_slack import FakeSlack

    http = FakeHTTP().install(monkeypatch)
    slack = FakeSlack().attach(http)
    p = dict(pick(), active_title="Alpha", comp_title="Classic", angle_category="franchise_sibling")
    post_pick("xoxb", "C1", p, {"domestic_today": 150_000_000, "worldwide_today": 350_000_000}, dict(COMP_NO_DOM, release_year="2010"))
    text = json.dumps(slack.messages[0]["blocks"])
    assert "Dom: n/a" in text and "$0" not in text


# ── legacy full build is opt-in only ────────────────────────────────────

def test_legacy_full_build_refuses_without_explicit_flag(monkeypatch):
    spec = importlib.util.spec_from_file_location("build_corpus", os.path.join(ROOT, "scripts", "build_corpus.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    called = []
    monkeypatch.setattr(mod, "main", lambda: called.append(True))
    assert mod.entrypoint([]) == 3 and called == []
    assert mod.entrypoint(["--legacy-full-build"]) == 0 and called == [True]
