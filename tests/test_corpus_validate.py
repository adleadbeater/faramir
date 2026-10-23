"""Increment 1: schema-v2 corpus integrity gates."""

import pytest

from faramir.corpus_validate import coverage, validate_corpus


def film(i, dom=100_000_000, ww=250_000_000, **kw):
    f = {
        "imdb_id": f"tt{i:07d}", "tmdb_id": i, "identity_status": "verified",
        "domestic_lifetime": dom, "domestic_status": "reported" if dom else "missing",
        "worldwide_lifetime": ww, "worldwide_status": "reported" if ww else "missing",
    }
    f.update(kw)
    return f


def corpus(films, version=2):
    return {"meta": {"corpus_schema_version": version}, "films": films}


def good(n=1800):
    return [film(i) for i in range(1, n + 1)]


def codes(c, **kw):
    return {v.code for v in validate_corpus(c, **kw)}


def test_good_corpus_passes():
    assert validate_corpus(corpus(good())) == []


def test_v1_list_is_rejected():
    assert codes([film(1)]) == {"bad_format"}


def test_wrong_schema_version():
    assert "schema_version" in codes(corpus(good(), version=1))


def test_too_few_rows():
    assert "too_few_rows" in codes(corpus(good(1799)))


def test_shrink_vs_previous():
    prev = corpus(good(2000))
    assert "shrank" in codes(corpus(good(1850)), previous=prev)
    assert "shrank" not in codes(corpus(good(1950)), previous=prev)


def test_duplicate_imdb_id():
    films = good()
    films[1]["imdb_id"] = films[0]["imdb_id"]
    assert "duplicate_imdb_id" in codes(corpus(films))


def test_duplicate_verified_tmdb_id():
    films = good()
    films[1]["tmdb_id"] = films[0]["tmdb_id"]
    assert "duplicate_tmdb_id" in codes(corpus(films))


def test_unresolved_rows_may_share_null_tmdb_id():
    films = good()
    for f in films[:2]:
        f.update(tmdb_id=None, identity_status="unresolved")
    assert validate_corpus(corpus(films)) == []


def test_ww_below_dom_among_reported():
    films = good()
    films[0].update(domestic_lifetime=300_000_000, worldwide_lifetime=200_000_000)
    assert "ww_below_dom" in codes(corpus(films))


def test_ww_below_dom_ignored_when_dom_missing():
    films = good()
    films[0].update(domestic_lifetime=None, domestic_status="missing", worldwide_lifetime=50_000_000)
    assert validate_corpus(corpus(films)) == []


@pytest.mark.parametrize("patch", [
    {"domestic_status": "reported", "domestic_lifetime": None},
    {"domestic_status": "reported", "domestic_lifetime": 0},
    {"domestic_status": "missing", "domestic_lifetime": 5},
    {"domestic_status": "confirmed_zero", "domestic_lifetime": None},
    {"domestic_status": "zero"},
])
def test_metric_status_value_mismatch(patch):
    films = good()
    films[0].update(patch)
    assert "metric_status" in codes(corpus(films))


def test_identity_status_problems():
    films = good()
    films[0]["identity_status"] = "guessed"
    films[1]["tmdb_id"] = None
    assert "identity_status" in codes(corpus(films))


def test_violation_details_are_capped():
    films = good()
    for f in films[:20]:
        f["imdb_id"] = "tt0000001"
    (v,) = [v for v in validate_corpus(corpus(films)) if v.code == "duplicate_imdb_id"]
    assert "x20" in v.detail


def test_coverage_counts():
    films = [film(1), film(2, dom=None), film(3, ww=None)]
    cov = coverage(films)
    assert cov["domestic"]["reported"] == 2 and cov["domestic"]["missing"] == 1
    assert cov["worldwide"]["reported_share"] == pytest.approx(0.6667, abs=1e-4)
