"""Increment 1: TMDB-only corpus identity repair."""

import csv
import importlib.util
import json
import os

import pytest

from faramir.corpus_repair import build_v2, is_classic, repair_films
from faramir.corpus_validate import validate_corpus
from tests.fakes.fake_http import FakeHTTP, FakeResponse

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
AUDIT_CSV = os.path.join(ROOT, "docs", "v2", "corpus_identity_audit_2026-10-22.csv")


def v1_row(imdb_id, tmdb_id, title, year, dom=100_000_000, ww=250_000_000, **kw):
    row = {
        "tmdb_id": tmdb_id, "imdb_id": imdb_id, "title": title, "release_date": f"{year}-06-01",
        "release_year": str(year), "genres": ["Action"], "director": "Wrong Director",
        "lead_cast": ["Wrong Star"], "collection_id": 1, "collection_name": "Wrong Collection",
        "origin_country": "US", "distributor": "Wrong Co", "domestic_lifetime": dom,
        "worldwide_lifetime": ww, "bom_gross": ww or dom, "bom_rank": 1,
        "is_best_picture": False, "is_classic": False,
    }
    row.update(kw)
    return row


def details_for(table):
    def details(tmdb_id):
        d = table[tmdb_id]
        return {"tmdb_id": tmdb_id, "imdb_id": d.get("imdb_id", ""), "title": d["title"],
                "release_date": d["date"], "release_year": d["date"][:4], "genres": d.get("genres", ["Drama"]),
                "director": d.get("director", "True Director"), "lead_cast": ["True Star"],
                "collection_id": d.get("collection_id"), "collection_name": d.get("collection_name", ""),
                "origin_country": "US", "distributor": "True Co"}
    return details


def find_for(table):
    return lambda imdb_id: table.get(imdb_id, [])


# ── identity cases ───────────────────────────────────────────────────────

def test_find_beats_title_search_for_remake():
    # tt6105098 was labelled The Lion King (1994, tmdb 8587); /find says the 2019 film.
    films = [v1_row("tt6105098", 8587, "The Lion King", 1994, 543_638_043, 1_662_020_819)]
    out, changes = repair_films(
        films,
        find_for({"tt6105098": [{"id": 420818}]}),
        details_for({420818: {"title": "The Lion King", "date": "2019-07-12", "imdb_id": "tt6105098"}}),
        checked_at="2026-10-22",
    )
    row = out[0]
    assert row["tmdb_id"] == 420818 and row["release_year"] == "2019"
    assert row["identity_status"] == "verified"
    assert row["domestic_lifetime"] == 543_638_043 and row["worldwide_lifetime"] == 1_662_020_819
    assert row["director"] == "True Director" and row["collection_name"] == ""
    assert changes[0]["change"] == "relabelled" and changes[0]["old_tmdb_id"] == 8587


def test_sequel_does_not_absorb_original():
    films = [v1_row("tt0369610", 1234821, "Jurassic World Rebirth", 2025, 653_406_625, 1_671_537_444)]
    out, _ = repair_films(
        films,
        find_for({"tt0369610": [{"id": 135397}]}),
        details_for({135397: {"title": "Jurassic World", "date": "2015-06-12"}}),
        checked_at="2026-10-22",
    )
    assert (out[0]["tmdb_id"], out[0]["title"], out[0]["release_year"]) == (135397, "Jurassic World", "2015")


def test_same_title_same_year_uses_imdb_linked_record():
    films = [v1_row("tt32553390", 1019828, "A Place Called Silence", 2024, 0, 174_308_156)]
    out, _ = repair_films(
        films,
        find_for({"tt32553390": [{"id": 1300945}]}),
        details_for({1300945: {"title": "A Place Called Silence", "date": "2024-04-30"}}),
        checked_at="2026-10-22",
    )
    assert out[0]["tmdb_id"] == 1300945


def test_correct_row_is_kept_without_details_call():
    films = [v1_row("tt0468569", 155, "The Dark Knight", 2008, 534_987_076, 1_008_480_529, director="Christopher Nolan")]

    def no_details(_):
        raise AssertionError("details should not be fetched for an unchanged identity")

    out, changes = repair_films(films, find_for({"tt0468569": [{"id": 155}]}), no_details, checked_at="2026-10-22")
    assert out[0]["director"] == "Christopher Nolan" and out[0]["identity_status"] == "verified"
    assert changes == []


@pytest.mark.parametrize("results", [[], [{"id": 1}, {"id": 2}]])
def test_unresolved_row_keeps_grosses_drops_identity(results):
    films = [v1_row("tt0000001", 777, "Some Label", 2001, 120_000_000, 300_000_000)]
    out, changes = repair_films(films, find_for({"tt0000001": results}), details_for({}), checked_at="2026-10-22")
    row = out[0]
    assert row["identity_status"] == "unresolved" and row["tmdb_id"] is None
    assert row["director"] == "" and row["lead_cast"] == [] and row["genres"] == [] and row["collection_id"] is None
    assert row["domestic_lifetime"] == 120_000_000 and row["worldwide_lifetime"] == 300_000_000
    assert changes[0]["change"] == "unresolved"


def test_details_imdb_mismatch_is_unresolved_not_guessed():
    films = [v1_row("tt0000002", 5, "Label", 1999)]
    out, _ = repair_films(
        films,
        find_for({"tt0000002": [{"id": 6}]}),
        details_for({6: {"title": "Other", "date": "1999-01-01", "imdb_id": "tt9999999"}}),
        checked_at="2026-10-22",
    )
    assert out[0]["identity_status"] == "unresolved"


def test_lookup_failure_propagates():
    def broken(_):
        raise RuntimeError("TMDB down")

    with pytest.raises(RuntimeError):
        repair_films([v1_row("tt0000003", 1, "X", 2000)], broken, details_for({}), checked_at="2026-10-22")


# ── metric availability ─────────────────────────────────────────────────

def test_zero_grosses_become_missing_never_confirmed_zero():
    films = [v1_row("tt0113071", 6520, "First Knight", 1995, 0, 127_600_435), v1_row("tt0000004", 4, "No WW", 2000, 90_000_000, 0)]
    out, _ = repair_films(films, find_for({"tt0113071": [{"id": 6520}], "tt0000004": [{"id": 4}]}), details_for({}), checked_at="2026-10-22")
    assert out[0]["domestic_lifetime"] is None and out[0]["domestic_status"] == "missing"
    assert out[0]["worldwide_status"] == "reported"
    assert out[1]["worldwide_lifetime"] is None and out[1]["worldwide_status"] == "missing"
    assert not any(r[f"{m}_status"] == "confirmed_zero" for r in out for m in ("domestic", "worldwide"))


def test_is_classic_recomputed_from_true_year():
    assert is_classic("1994", 183_031_272) is False
    assert is_classic("1978", 300_478_449) is True
    assert is_classic("1993", 1_103_136_596) is True
    assert is_classic("2022", 1_503_260_455) is False


# ── full offline reproduction of the audit ──────────────────────────────

def _committed_v1():
    with open(os.path.join(ROOT, "data", "corpus.json")) as f:
        return json.load(f)


def _recorded_find():
    with open(os.path.join(FIXTURES, "tmdb_find_corpus_2026-10-22.json")) as f:
        return json.load(f)


def _details_from_find(recorded):
    by_id = {m["id"]: m for results in recorded.values() for m in results}

    def details(tmdb_id):
        m = by_id[tmdb_id]
        return {"tmdb_id": tmdb_id, "title": m["title"], "release_date": m["release_date"],
                "release_year": (m["release_date"] or "")[:4], "genres": [], "director": "",
                "lead_cast": [], "collection_id": None, "collection_name": "", "origin_country": "", "distributor": ""}
    return details


def test_offline_repair_reproduces_audit_exactly():
    v1 = _committed_v1()
    recorded = _recorded_find()
    out, changes = repair_films(v1, find_for(recorded), _details_from_find(recorded), checked_at="2026-10-22", workers=1)

    with open(AUDIT_CSV, newline="", encoding="utf-8") as f:
        audit = {r["imdb_id"]: int(r["verified_tmdb_id"]) for r in csv.DictReader(f)}

    relabelled = {c["imdb_id"]: c["new_tmdb_id"] for c in changes if c["change"] == "relabelled"}
    assert len(audit) == 322
    assert relabelled == audit
    assert not [c for c in changes if c["change"] == "unresolved"]

    # Grosses unchanged (zeros become None/missing), row order and count preserved.
    assert [r["imdb_id"] for r in out] == [r["imdb_id"] for r in v1]
    for old, new in zip(v1, out):
        for m in ("domestic", "worldwide"):
            assert new[f"{m}_lifetime"] == (old[f"{m}_lifetime"] or None)

    corpus = build_v2(out, "2026-10-01", "2026-10-22T00:00:00+00:00", "data/corpus.json")
    assert validate_corpus(corpus) == []
    cov = corpus["meta"]["coverage"]
    assert (cov["domestic"]["reported"], cov["domestic"]["missing"]) == (1000, 1011)
    assert (cov["worldwide"]["reported"], cov["worldwide"]["missing"]) == (1999, 12)


def test_committed_v1_corpus_fails_v2_gates():
    # The unrepaired corpus is not even v2; wrapped as v2 it still fails on duplicates.
    v1 = _committed_v1()
    wrapped = {"meta": {"corpus_schema_version": 2}, "films": [
        dict(f, identity_status="verified",
             domestic_status="reported" if f["domestic_lifetime"] else "missing",
             worldwide_status="reported" if f["worldwide_lifetime"] else "missing",
             domestic_lifetime=f["domestic_lifetime"] or None,
             worldwide_lifetime=f["worldwide_lifetime"] or None)
        for f in v1]}
    codes = {v.code for v in validate_corpus(wrapped)}
    assert "duplicate_tmdb_id" in codes


# ── TMDB client ─────────────────────────────────────────────────────────

def test_find_by_imdb_retries_then_succeeds(monkeypatch):
    from faramir import tmdb

    monkeypatch.setenv("TMDB_API_KEY", "test-key")
    monkeypatch.setattr(tmdb.time, "sleep", lambda s: None)
    responses = [FakeResponse(429, {}), FakeResponse(503, {}), FakeResponse(200, {"movie_results": [{"id": 9359}]})]
    http = FakeHTTP().install(monkeypatch)
    http.route("GET", "https://api.themoviedb.org/3/find/tt0110478", lambda **_: responses.pop(0))
    assert tmdb.find_by_imdb("tt0110478") == [{"id": 9359}]
    assert http.calls[0]["params"]["external_source"] == "imdb_id"


def test_find_by_imdb_gives_up_without_leaking_key(monkeypatch):
    from faramir import tmdb

    monkeypatch.setenv("TMDB_API_KEY", "secret-key-value")
    monkeypatch.setattr(tmdb.time, "sleep", lambda s: None)
    http = FakeHTTP().install(monkeypatch)
    http.route("GET", "https://api.themoviedb.org/3/find/", FakeResponse(503, {}))
    with pytest.raises(RuntimeError) as exc:
        tmdb.find_by_imdb("tt0000001")
    assert "secret-key-value" not in str(exc.value)
    assert len(http.calls) == tmdb.MAX_ATTEMPTS


# ── CLI: never writes on gate failure ───────────────────────────────────

def _load_build_corpus():
    spec = importlib.util.spec_from_file_location("build_corpus", os.path.join(ROOT, "scripts", "build_corpus.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_tmdb(monkeypatch, find, details):
    from faramir import tmdb

    monkeypatch.setattr(tmdb, "find_by_imdb", find)
    monkeypatch.setattr(tmdb, "get_movie_details", details)


def test_cli_refuses_to_write_when_a_gate_fails(tmp_path, monkeypatch):
    mod = _load_build_corpus()
    src = tmp_path / "corpus.json"
    src.write_text(json.dumps([v1_row("tt0000010", 10, "Only Film", 2000)]))  # 1 row < 1,800
    _patch_tmdb(monkeypatch, find_for({"tt0000010": [{"id": 10}]}), details_for({}))
    out = tmp_path / "corpus.v2.json"
    rc = mod.repair_main(["--repair-identities", "--input", str(src), "--output", str(out),
                          "--gross-as-of", "2026-10-01", "--report-dir", str(tmp_path / "report")])
    assert rc == 1
    assert not out.exists()
    report = (tmp_path / "report" / "report.md").read_text()
    assert "too_few_rows" in report and "NOT written" in report


def test_cli_writes_v2_when_gates_pass(tmp_path, monkeypatch):
    mod = _load_build_corpus()
    rows = [v1_row(f"tt{i:07d}", i, f"Film {i}", 2000) for i in range(1, 1801)]
    src = tmp_path / "corpus.json"
    src.write_text(json.dumps(rows))
    _patch_tmdb(monkeypatch, find_for({r["imdb_id"]: [{"id": r["tmdb_id"]}] for r in rows}), details_for({}))
    out = tmp_path / "corpus.v2.json"
    rc = mod.repair_main(["--repair-identities", "--input", str(src), "--output", str(out),
                          "--gross-as-of", "2026-10-01", "--report-dir", str(tmp_path / "report")])
    assert rc == 0
    corpus = json.loads(out.read_text())
    assert corpus["meta"]["corpus_schema_version"] == 2 and len(corpus["films"]) == 1800
    assert src.read_text() == json.dumps(rows)  # input untouched
