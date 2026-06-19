"""Unit tests for milestone.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from faramir.milestone import find_milestones

THRESHOLDS = [100_000_000, 200_000_000, 500_000_000, 1_000_000_000]
PROXIMITY = 0.05


def make_active(domestic_today=0, domestic_yesterday=0, worldwide_today=0, worldwide_yesterday=0):
    return {
        "tmdb_id": 1,
        "canonical_title": "Test Film",
        "domestic_today": domestic_today,
        "domestic_yesterday": domestic_yesterday,
        "worldwide_today": worldwide_today,
        "worldwide_yesterday": worldwide_yesterday,
    }


# --- just_passed tests ---

def test_just_passed_domestic():
    film = make_active(domestic_today=101_000_000, domestic_yesterday=99_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    just_passed = [r for r in results if r["kind"] == "milestone" and r["milestone_type"] == "just_passed" and r["axis"] == "domestic"]
    assert len(just_passed) == 1
    assert just_passed[0]["threshold_value"] == 100_000_000


def test_just_passed_worldwide():
    film = make_active(worldwide_today=205_000_000, worldwide_yesterday=195_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    just_passed = [r for r in results if r["milestone_type"] == "just_passed" and r["axis"] == "worldwide"]
    assert len(just_passed) == 1
    assert just_passed[0]["threshold_value"] == 200_000_000


def test_just_passed_not_triggered_when_already_past_yesterday():
    # Both yesterday and today above threshold — not just-passed
    film = make_active(domestic_today=110_000_000, domestic_yesterday=105_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    just_passed = [r for r in results if r["milestone_type"] == "just_passed" and r["threshold_value"] == 100_000_000]
    assert len(just_passed) == 0


# --- about_to_pass tests ---

def test_about_to_pass_domestic():
    # Within 5% of $200M but not crossed
    film = make_active(domestic_today=192_000_000, domestic_yesterday=188_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    about_to = [r for r in results if r["milestone_type"] == "about_to_pass" and r["axis"] == "domestic"]
    assert len(about_to) == 1
    assert about_to[0]["threshold_value"] == 200_000_000


def test_about_to_pass_worldwide():
    film = make_active(worldwide_today=490_000_000, worldwide_yesterday=480_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    about_to = [r for r in results if r["milestone_type"] == "about_to_pass" and r["axis"] == "worldwide"]
    assert len(about_to) == 1
    assert about_to[0]["threshold_value"] == 500_000_000


def test_about_to_pass_not_triggered_when_above():
    # Already crossed
    film = make_active(domestic_today=510_000_000, domestic_yesterday=495_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    about_to = [r for r in results if r["milestone_type"] == "about_to_pass" and r["threshold_value"] == 500_000_000]
    assert len(about_to) == 0


def test_about_to_pass_not_triggered_when_too_far():
    # More than 5% below threshold
    film = make_active(domestic_today=180_000_000, domestic_yesterday=175_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    about_to = [r for r in results if r["milestone_type"] == "about_to_pass" and r["threshold_value"] == 200_000_000]
    assert len(about_to) == 0


# --- boundary tests ---

def test_exactly_at_threshold_is_just_passed():
    film = make_active(domestic_today=100_000_000, domestic_yesterday=95_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    just_passed = [r for r in results if r["milestone_type"] == "just_passed" and r["threshold_value"] == 100_000_000]
    assert len(just_passed) == 1


def test_exactly_at_lower_band_is_about_to_pass():
    # Exactly at threshold * (1 - 0.05) = 95_000_000
    film = make_active(domestic_today=95_000_000, domestic_yesterday=90_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    about_to = [r for r in results if r["milestone_type"] == "about_to_pass" and r["threshold_value"] == 100_000_000]
    assert len(about_to) == 1


def test_both_axes_both_milestones():
    # Film approaching $200M domestic and just crossed $500M worldwide
    film = make_active(
        domestic_today=192_000_000,
        domestic_yesterday=188_000_000,
        worldwide_today=505_000_000,
        worldwide_yesterday=495_000_000,
    )
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    dom_about_to = [r for r in results if r["axis"] == "domestic" and r["milestone_type"] == "about_to_pass"]
    ww_just_passed = [r for r in results if r["axis"] == "worldwide" and r["milestone_type"] == "just_passed"]
    assert len(dom_about_to) == 1
    assert len(ww_just_passed) == 1


def test_no_milestones_when_far_below():
    film = make_active(domestic_today=50_000_000, domestic_yesterday=48_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    assert results == []


def test_richness_score_is_9():
    film = make_active(domestic_today=101_000_000, domestic_yesterday=99_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    for r in results:
        assert r["richness_score"] == 9


def test_suppressed_is_false():
    film = make_active(domestic_today=101_000_000, domestic_yesterday=99_000_000)
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    for r in results:
        assert r["suppressed"] is False


def test_multiple_milestones_at_once():
    # Film crossing $100M yesterday and hitting about-to-pass $200M
    film = make_active(
        domestic_today=192_000_000,
        domestic_yesterday=98_000_000,
    )
    results = find_milestones(film, THRESHOLDS, PROXIMITY)
    # Should get just_passed for $100M AND about_to_pass for $200M
    just_100 = [r for r in results if r["threshold_value"] == 100_000_000 and r["milestone_type"] == "just_passed"]
    about_200 = [r for r in results if r["threshold_value"] == 200_000_000 and r["milestone_type"] == "about_to_pass"]
    assert len(just_100) == 1
    assert len(about_200) == 1
