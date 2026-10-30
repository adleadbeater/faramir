"""Increment 2: v2 corpus loading, health classes and the stale-data rule."""

import copy
import json
import os
from datetime import date

import pytest

from faramir.corpus import (
    ABORT,
    DEGRADED,
    OK,
    load_corpus_v2,
    reported,
    stale_evidence,
)
from faramir.corpus_validate import coverage

ROOT = os.path.join(os.path.dirname(__file__), "..")
V2_PATH = os.path.join(ROOT, "data", "corpus.v2.json")
RUN = date(2026, 10, 23)


def film(i, dom=150_000_000, ww=400_000_000, title=None, **kw):
    f = {
        "imdb_id": f"tt{i:07d}", "tmdb_id": i, "identity_status": "verified", "title": title or f"Film {i}",
        "release_year": "2010", "genres": ["Drama"],
        "domestic_lifetime": dom, "domestic_status": "reported" if dom else "missing",
        "worldwide_lifetime": ww, "worldwide_status": "reported" if ww else "missing",
    }
    f.update(kw)
    return f


def write(tmp_path, films, gross_as_of="2026-10-01", **meta_overrides):
    meta = {"corpus_schema_version": 2, "gross_as_of": gross_as_of, "rows": len(films), "coverage": coverage(films)}
    meta.update(meta_overrides)
    p = tmp_path / "corpus.v2.json"
    p.write_text(json.dumps({"meta": meta, "films": films}))
    return str(p)


def healthy(n=2000):
    return [film(i) for i in range(1, n + 1)]


# ── the real repaired corpus ────────────────────────────────────────────

def test_repaired_corpus_is_ok_despite_half_domestic_coverage():
    load = load_corpus_v2(V2_PATH, RUN)
    assert load.status == OK, load.reasons
    assert len(load.films) == 2011 and load.exclusions == []
    assert load.coverage["domestic"]["reported_share"] == pytest.approx(0.4973)
    assert load.editor_warning() is None


def test_repaired_corpus_degrades_with_age_and_warns_editors():
    load = load_corpus_v2(V2_PATH, date(2026, 11, 20))
    assert load.status == DEGRADED and any(r.startswith("corpus_age") for r in load.reasons)
    assert "as of 2026-10-01 (50 days old)" in load.editor_warning()
    assert len(load.films) == 2011  # age alone excludes nothing


# ── legacy / malformed files: ABORT with a clear diagnostic ─────────────

def test_legacy_v1_list_is_rejected_not_used(tmp_path):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps([film(1)]))
    load = load_corpus_v2(str(p), RUN)
    assert load.status == ABORT and load.films == []
    assert load.reasons[0].startswith("legacy_format") and "--repair-identities" in load.reasons[0]


def test_committed_legacy_corpus_file_is_rejected():
    load = load_corpus_v2(os.path.join(ROOT, "data", "corpus.json"), RUN)
    assert load.status == ABORT and load.reasons[0].startswith("legacy_format")


@pytest.mark.parametrize("content, prefix", [
    (None, "corpus_missing"),
    ("{not json", "corpus_unreadable"),
    (json.dumps({"films": []}), "bad_format"),
])
def test_missing_unreadable_or_malformed(tmp_path, content, prefix):
    p = tmp_path / "c.json"
    if content is not None:
        p.write_text(content)
    load = load_corpus_v2(str(p), RUN)
    assert load.status == ABORT and load.reasons[0].startswith(prefix)


def test_wrong_schema_version(tmp_path):
    load = load_corpus_v2(write(tmp_path, healthy(), corpus_schema_version=3), RUN)
    assert load.status == ABORT and load.reasons[0].startswith("schema_version")


@pytest.mark.parametrize("gross_as_of, prefix", [("not-a-date", "meta_missing"), ("2026-12-01", "gross_as_of_in_future")])
def test_bad_gross_as_of(tmp_path, gross_as_of, prefix):
    load = load_corpus_v2(write(tmp_path, healthy(), gross_as_of=gross_as_of), RUN)
    assert load.status == ABORT and load.reasons[0].startswith(prefix)


def test_meta_mismatch_detects_truncation(tmp_path):
    films = healthy()
    p = write(tmp_path, films)
    data = json.loads(open(p).read())
    data["films"] = data["films"][:-50]  # truncated after meta was written
    open(p, "w").write(json.dumps(data))
    load = load_corpus_v2(p, RUN)
    assert load.status == ABORT and load.reasons[0].startswith("meta_mismatch")


# ── health classes ──────────────────────────────────────────────────────

def test_ok(tmp_path):
    load = load_corpus_v2(write(tmp_path, healthy()), RUN)
    assert load.status == OK and load.reasons == [] and len(load.films) == 2000


def test_duplicate_identity_aborts(tmp_path):
    films = healthy()
    films[1]["tmdb_id"] = films[0]["tmdb_id"]
    load = load_corpus_v2(write(tmp_path, films), RUN)
    assert load.status == ABORT and any(r.startswith("duplicate_tmdb_id") for r in load.reasons)
    films = healthy()
    films[1]["imdb_id"] = films[0]["imdb_id"]
    assert load_corpus_v2(write(tmp_path, films), RUN).status == ABORT


def _invalidate(films, n):
    for f in films[:n]:
        f["worldwide_lifetime"] = f["domestic_lifetime"] - 1  # ww < dom, statuses unchanged
    return films


def test_few_invalid_rows_degrade_and_are_excluded(tmp_path):
    films = _invalidate(healthy(), 20)  # 1.0% — at the limit
    load = load_corpus_v2(write(tmp_path, films), RUN)
    assert load.status == DEGRADED
    assert len(load.films) == 1980
    assert {f["imdb_id"] for f in load.films}.isdisjoint({f["imdb_id"] for f in films[:20]})
    assert load.exclusion_counts() == {"invalid": 20}
    assert "20 comparison record(s) excluded (invalid: 20)" in load.editor_warning()


def test_many_invalid_rows_abort(tmp_path):
    load = load_corpus_v2(write(tmp_path, _invalidate(healthy(), 21)), RUN)  # 1.05%
    assert load.status == ABORT and any(r.startswith("invalid_share") for r in load.reasons)
    assert load.films == []


def _unresolve(films, n):
    for f in films[:n]:
        f.update(identity_status="unresolved", tmdb_id=None)
    return films


def test_few_unresolved_degrade(tmp_path):
    load = load_corpus_v2(write(tmp_path, _unresolve(healthy(), 40)), RUN)  # 2.0%
    assert load.status == DEGRADED and load.exclusion_counts() == {"unresolved_identity": 40}


def test_many_unresolved_abort(tmp_path):
    load = load_corpus_v2(write(tmp_path, _unresolve(healthy(), 41)), RUN)
    assert load.status == ABORT and any(r.startswith("unresolved_share") for r in load.reasons)


def test_too_few_usable_rows_abort(tmp_path):
    load = load_corpus_v2(write(tmp_path, healthy(1799)), RUN)
    assert load.status == ABORT and any(r.startswith("too_few_usable_rows") for r in load.reasons)


def test_low_worldwide_coverage_aborts(tmp_path):
    films = healthy()
    for f in films[:101]:  # 94.95% worldwide reported
        f.update(worldwide_lifetime=None, worldwide_status="missing")
    load = load_corpus_v2(write(tmp_path, films), RUN)
    assert load.status == ABORT and any(r.startswith("worldwide_coverage") for r in load.reasons)


def test_low_domestic_coverage_alone_is_ok(tmp_path):
    films = healthy()
    for f in films[:1100]:  # 45% domestic reported
        f.update(domestic_lifetime=None, domestic_status="missing")
    load = load_corpus_v2(write(tmp_path, films), RUN)
    assert load.status == OK and len(load.films) == 2000


# ── stale rule ──────────────────────────────────────────────────────────

AS_OF = date(2026, 10, 1)


def test_s1_state_evidence_after_gross_date_marks_stale():
    films = [film(1)]
    state = [{"imdb_id": "tt0000001", "tmdb_id": 1, "last_seen": "2026-10-20"}]
    assert stale_evidence(films, AS_OF, state_rows=state) == {"tt0000001": "S1_tracked_on_chart_after_gross_date"}


def test_s1_falls_back_to_tmdb_id_only_when_state_has_no_imdb():
    films = [film(1)]
    assert stale_evidence(films, AS_OF, state_rows=[{"imdb_id": "", "tmdb_id": "1", "last_seen": "2026-10-20"}])
    # A state row with a different imdb_id is not evidence even if tmdb_id collides.
    assert stale_evidence(films, AS_OF, state_rows=[{"imdb_id": "tt9999999", "tmdb_id": 1, "last_seen": "2026-10-20"}]) == {}


@pytest.mark.parametrize("last_seen", ["2026-10-01", "2026-10-03", "", "garbage"])
def test_s1_no_evidence_on_or_before_gross_date(last_seen):
    # last_seen 10-03 → chart date may be 10-01 (D-2): not provably after gross_as_of.
    assert stale_evidence([film(1)], AS_OF, state_rows=[{"imdb_id": "tt0000001", "last_seen": last_seen}]) == {}


def test_false_positive_guard_recent_release_without_chart_evidence_is_not_stale():
    # Released two weeks before the gross date, but nothing shows it still earning.
    recent = film(1, release_date="2026-09-17", release_year="2026")
    assert stale_evidence([recent], AS_OF, state_rows=[], chart_rows=[], chart_date=date(2026, 10, 21)) == {}


def test_false_negative_guard_old_film_rereleased_is_stale():
    # A 1985 film back on the chart (e.g. a re-release) with a cumulative total
    # consistent with its lifetime gross: evidence its gross moved.
    classic = film(1, title="Back to the Future", dom=212_000_000, release_year="1985")
    chart = [{"title": "Back to the Future", "gross_to_date": 225_267_287}]
    assert stale_evidence([classic], AS_OF, chart_rows=chart, chart_date=date(2026, 10, 21)) == {
        "tt0000001": "S2_title_on_chart_after_gross_date"
    }


def test_s2_title_match_with_inconsistent_gross_is_not_evidence():
    # A new film sharing an old title with a far smaller total is a different film.
    old = film(1, title="Superman", dom=134_478_449)
    chart = [{"title": "Superman", "gross_to_date": 20_000_000}]
    assert stale_evidence([old], AS_OF, chart_rows=chart, chart_date=date(2026, 10, 21)) == {}


def test_s2_old_title_with_much_larger_new_total_is_a_different_film():
    # Superman (1978, $134M) vs a 2026 film of the same name at $300M: a remake,
    # not evidence that the 1978 gross moved.
    old = film(1, title="Superman", dom=134_478_449, release_year="1978")
    chart = [{"title": "Superman", "gross_to_date": 300_000_000}]
    assert stale_evidence([old], AS_OF, chart_rows=chart, chart_date=date(2026, 10, 21)) == {}


def test_s2_recent_title_can_grow_without_upper_bound():
    # Resident Evil (2026): $108M at the gross date, $150M on today's chart.
    recent = film(1, title="Resident Evil", dom=108_312_282, release_year="2026")
    chart = [{"title": "Resident Evil", "gross_to_date": 150_249_362}]
    assert stale_evidence([recent], AS_OF, chart_rows=chart, chart_date=date(2026, 10, 21))


def test_s2_missing_domestic_is_not_evidence():
    f = film(1, title="Some Import", dom=None)
    chart = [{"title": "Some Import", "gross_to_date": 50_000_000}]
    assert stale_evidence([f], AS_OF, chart_rows=chart, chart_date=date(2026, 10, 21)) == {}


def test_s2_chart_dated_on_gross_date_is_not_evidence():
    f = film(1, title="Film One")
    chart = [{"title": "Film One", "gross_to_date": 200_000_000}]
    assert stale_evidence([f], AS_OF, chart_rows=chart, chart_date=AS_OF) == {}


def test_stale_records_are_excluded_but_do_not_degrade(tmp_path):
    films = healthy()
    films[0].update(title="Back to the Future", domestic_lifetime=212_000_000)
    p = write(tmp_path, films)
    load = load_corpus_v2(p, RUN, chart_rows=[{"title": "Back to the Future", "gross_to_date": 225_000_000}], chart_date=date(2026, 10, 21))
    assert load.status == OK
    assert load.exclusion_counts() == {"stale": 1} and len(load.films) == 1999


def test_reported_helper_never_returns_zero_for_missing():
    assert reported({"domestic_lifetime": None, "domestic_status": "missing"}, "domestic") is None
    assert reported({"domestic_lifetime": 0, "domestic_status": "confirmed_zero"}, "domestic") is None
    assert reported({"domestic_lifetime": 5, "domestic_status": "reported"}, "domestic") == 5
    assert reported({"domestic_lifetime": 0}, "domestic") is None  # legacy v1 zero
    assert reported({"domestic_lifetime": 7}, "domestic") == 7
