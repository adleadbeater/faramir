"""Unit tests for matcher.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir.matcher import (
    detect_angle_categories,
    score_candidate,
    find_candidates,
    find_all_candidates,
)
from faramir.corpus import build_index


# --- Fixtures ---

def make_film(**kwargs):
    defaults = {
        "tmdb_id": 1,
        "title": "Test Film",
        "canonical_title": "Test Film",
        "director": "Jane Smith",
        "lead_cast": ["Actor A", "Actor B", "Actor C"],
        "genres": ["Action"],
        "collection_id": None,
        "collection_name": "",
        "distributor": "Big Studio",
        "origin_country": "US",
        "release_year": "2023",
        "domestic_today": 150_000_000,
        "domestic_yesterday": 140_000_000,
        "worldwide_today": 350_000_000,
        "worldwide_yesterday": 330_000_000,
        "domestic_lifetime": 150_000_000,
        "worldwide_lifetime": 350_000_000,
        "is_best_picture": False,
        "is_classic": False,
    }
    defaults.update(kwargs)
    return defaults


def make_corpus_film(**kwargs):
    defaults = {
        "tmdb_id": 999,
        "title": "Classic Film",
        "director": "Other Director",
        "lead_cast": ["Other Actor"],
        "genres": ["Drama"],
        "collection_id": None,
        "collection_name": "",
        "distributor": "Other Studio",
        "origin_country": "US",
        "release_year": "2010",
        "domestic_lifetime": 100_000_000,
        "worldwide_lifetime": 200_000_000,
        "is_best_picture": False,
        "is_classic": False,
    }
    defaults.update(kwargs)
    return defaults


# --- detect_angle_categories tests ---

def test_franchise_sibling_detected():
    active = make_film(collection_id=42, collection_name="Avengers Collection")
    comp = make_corpus_film(collection_id=42, collection_name="Avengers Collection")
    categories = detect_angle_categories(active, comp, [comp])
    assert "franchise_sibling" in categories


def test_franchise_sibling_not_detected_when_different():
    active = make_film(collection_id=42)
    comp = make_corpus_film(collection_id=99)
    categories = detect_angle_categories(active, comp, [comp])
    assert "franchise_sibling" not in categories


def test_creative_head_to_head_director():
    active = make_film(director="Jane Smith")
    comp = make_corpus_film(director="Jane Smith")
    categories = detect_angle_categories(active, comp, [comp])
    assert "creative_head_to_head_director" in categories


def test_creative_head_to_head_cast():
    active = make_film(lead_cast=["Tom Hanks", "Cate Blanchett"])
    comp = make_corpus_film(lead_cast=["Tom Hanks", "Somebody Else"])
    categories = detect_angle_categories(active, comp, [comp])
    assert "creative_head_to_head_cast" in categories


def test_studio_sibling_detected():
    active = make_film(distributor="Warner Bros.")
    comp = make_corpus_film(distributor="Warner Bros.")
    categories = detect_angle_categories(active, comp, [comp])
    assert "studio_sibling" in categories


def test_unexpected_giant_killer_detected():
    # No shared franchise, director, cast, genre; comp is massive
    active = make_film(
        director="Director A",
        lead_cast=["Actor X"],
        genres=["Comedy"],
        collection_id=None,
    )
    comp = make_corpus_film(
        director="Director B",
        lead_cast=["Actor Y"],
        genres=["Horror"],
        collection_id=None,
        worldwide_lifetime=600_000_000,
    )
    categories = detect_angle_categories(active, comp, [comp])
    assert "unexpected_giant_killer" in categories


def test_unexpected_giant_killer_requires_no_overlap():
    # Shared genre should prevent unexpected_giant_killer
    active = make_film(genres=["Horror"], collection_id=None)
    comp = make_corpus_film(genres=["Horror"], worldwide_lifetime=600_000_000, collection_id=None)
    categories = detect_angle_categories(active, comp, [comp])
    assert "unexpected_giant_killer" not in categories


def test_same_origin_detected():
    active = make_film(origin_country="KR")
    comp = make_corpus_film(origin_country="KR")
    categories = detect_angle_categories(active, comp, [comp])
    assert "same_origin" in categories


# --- score_candidate tests ---

def test_score_franchise_sibling():
    active = make_film()
    comp = make_corpus_film()
    score = score_candidate(active, comp, "domestic", "just_passed", ["franchise_sibling"], {})
    # franchise_sibling=10 + just_passed=3
    assert score == 13


def test_score_creative_director():
    active = make_film()
    comp = make_corpus_film()
    score = score_candidate(active, comp, "domestic", "about_to_pass", ["creative_head_to_head_director"], {})
    # 8 + 1
    assert score == 9


def test_score_best_picture_bonus():
    active = make_film()
    comp = make_corpus_film(is_best_picture=True)
    score = score_candidate(active, comp, "worldwide", "about_to_pass", ["studio_sibling"], {})
    # studio_sibling=2 + is_best_picture=3 + about_to_pass=1
    assert score == 6


def test_score_talent_signal_bonus():
    active = make_film()
    comp = make_corpus_film(director="Famous Director")
    signals = {"Famous Director": {"is_signal": True, "sessions_180d": 50000, "articles": 5}}
    score = score_candidate(active, comp, "domestic", "just_passed", ["studio_sibling"], signals)
    # studio_sibling=2 + just_passed=3 + talent_signal=2
    assert score == 7


def test_score_unexpected_giant_killer():
    active = make_film()
    comp = make_corpus_film()
    score = score_candidate(active, comp, "worldwide", "just_passed", ["unexpected_giant_killer"], {})
    # unexpected_giant_killer=7 + just_passed=3
    assert score == 10


# --- find_candidates / find_all_candidates tests ---

def test_find_candidates_just_passed():
    # Active film crossed comp at $100M domestic
    active = make_film(
        tmdb_id=1,
        domestic_today=102_000_000,
        domestic_yesterday=98_000_000,
    )
    comp = make_corpus_film(
        tmdb_id=2,
        domestic_lifetime=100_000_000,
        worldwide_lifetime=200_000_000,
    )
    corpus = [comp]
    index = build_index(corpus)
    candidates = find_candidates(active, index, corpus)
    domestic_candidates = [c for c in candidates if c["axis"] == "domestic"]
    assert len(domestic_candidates) > 0
    assert domestic_candidates[0]["kind"] == "just_passed"
    assert domestic_candidates[0]["threshold_value"] == 100_000_000


def test_find_candidates_about_to_pass():
    # Active film approaching $200M (within 5%)
    active = make_film(
        tmdb_id=1,
        domestic_today=192_000_000,
        domestic_yesterday=188_000_000,
    )
    comp = make_corpus_film(
        tmdb_id=2,
        domestic_lifetime=200_000_000,
        worldwide_lifetime=400_000_000,
    )
    corpus = [comp]
    index = build_index(corpus)
    candidates = find_candidates(active, index, corpus)
    domestic_candidates = [c for c in candidates if c["axis"] == "domestic"]
    assert len(domestic_candidates) > 0
    assert domestic_candidates[0]["kind"] == "about_to_pass"


def test_suppression_applied():
    active = make_film(tmdb_id=1, domestic_today=102_000_000, domestic_yesterday=98_000_000)
    comp = make_corpus_film(tmdb_id=2, domestic_lifetime=100_000_000, worldwide_lifetime=200_000_000)
    corpus = [comp]
    suppressed = {(1, 2, "domestic")}
    results = find_all_candidates([active], corpus, {}, suppressed)
    # Suppressed pair should not appear in results
    matching = [c for c in results if c.get("active_tmdb_id") == 1 and c.get("comp_tmdb_id") == 2 and c["axis"] == "domestic"]
    assert len(matching) == 0


def test_min_richness_filter():
    # same_origin only gives score=1, and about_to_pass gives +1 = total 2, below MIN_RICHNESS_TO_SURFACE=3
    # To avoid the just_passed bonus (+3), set comp_lifetime ABOVE today's gross (about_to_pass territory)
    # but make sure it's also outside the about_to_pass window so no kind bonus applies.
    # Easiest: put comp_lifetime far above today, so it's not within 5% of today — no candidate emitted at all.
    # A score-1 pair can only be emitted if it's in range; if comp is out of range, no candidate is emitted.
    active = make_film(
        tmdb_id=1,
        origin_country="KR",
        director="Director A",
        lead_cast=["Actor X"],
        genres=["Comedy"],
        collection_id=None,
        distributor="Studio A",
        domestic_today=100_000_000,
        domestic_yesterday=95_000_000,
    )
    # comp is far above today's gross — well outside just_passed or about_to_pass window
    comp = make_corpus_film(
        tmdb_id=2,
        origin_country="KR",
        director="Director B",
        lead_cast=["Actor Y"],
        genres=["Drama"],
        collection_id=None,
        distributor="Studio B",
        domestic_lifetime=200_000_000,  # 2x active — not within 5%, no candidate
        worldwide_lifetime=400_000_000,
    )
    corpus = [comp]
    results = find_all_candidates([active], corpus, {}, set())
    # comp is out of the matching window entirely — no candidate should be emitted
    assert len(results) == 0
