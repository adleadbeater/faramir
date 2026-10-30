"""Increment 9: active-film identity resolution. A title match alone never
establishes identity. Unit tests drive faramir.identity with fake TMDB
functions; integration tests run the daily pipeline on fakes."""

import json
from datetime import date

import pytest

from faramir.identity import ChartEntry, Identity, dedupe, is_rerelease, parse_slug, resolve
from tests.fakes.state_book import state_row

CHART = date(2026, 10, 21)


def entry(title="New Hit", slug="New-Hit-(2026)", days=10, chart=CHART):
    s, y = parse_slug(f"/movie/{slug}") if slug else ("", None)
    return ChartEntry(title, s, y, days, chart)


def result(tid, title, release_date, original_title=None):
    return {"id": tid, "title": title, "original_title": original_title or title, "release_date": release_date}


class TMDB:
    """Fake search/details/release_dates; records every call."""

    def __init__(self, results=(), details=None, release_dates=None, fail_details=False):
        self.results = list(results)
        self._details = details or {}
        self._release_dates = release_dates or {}
        self.fail_details = fail_details
        self.calls = []

    def search(self, title, year):
        self.calls.append(("search", title, year))
        return self.results

    def details(self, tid):
        self.calls.append(("details", tid))
        if self.fail_details:
            raise RuntimeError("boom")
        return self._details.get(tid, {"tmdb_id": tid, "imdb_id": f"tt{tid:07d}", "title": "x", "release_date": "2026-10-12"})

    def release_dates(self, tid):
        self.calls.append(("release_dates", tid))
        return self._release_dates.get(tid, {"results": []})


def run(e, tmdb, rows=(), overrides=None):
    return resolve(e, list(rows), overrides or {}, tmdb.search, tmdb.details, tmdb.release_dates)


# ── slugs and re-releases ───────────────────────────────────────────────

@pytest.mark.parametrize("href, slug, year", [
    ("/movie/Beta-Story-(2026)#tab=summary", "Beta-Story-(2026)", 2026),
    ("https://www.the-numbers.com/movie/Halloween-(2018)", "Halloween-(2018)", 2018),
    ("/movie/Godzilla-(2014-Japan)#tab=box-office", "Godzilla-(2014-Japan)", 2014),
    ("/movie/Avatar", "Avatar", None),
    ("", "", None),
])
def test_parse_slug(href, slug, year):
    assert parse_slug(href) == (slug, year)


def test_rerelease_rule():
    assert is_rerelease(1985, CHART) and is_rerelease(2024, CHART)
    assert not is_rerelease(2025, CHART) and not is_rerelease(2026, CHART) and not is_rerelease(None, CHART)


def test_rerelease_is_excluded_without_any_lookup():
    tmdb = TMDB([result(1, "Old Classic", "1985-06-01")])
    ident = run(entry("Old Classic", "Old-Classic-(1985)"), tmdb, rows=[state_row(1, "Old Classic", release_date="1985-06-01")])
    assert ident.status == "rerelease" and tmdb.calls == [] and ident.tmdb_id is None


# ── overrides ───────────────────────────────────────────────────────────

def test_override_tab_wins_over_state_and_search():
    tmdb = TMDB([result(1, "New Hit", "2026-10-12")])
    rows = [state_row(1, "New Hit", tn_slug="New-Hit-(2026)")]
    ident = run(entry(), tmdb, rows=rows, overrides={"New-Hit-(2026)": 777})
    assert (ident.status, ident.tmdb_id, ident.method, ident.confidence) == ("resolved", 777, "override", "manual")
    assert ("search", "New Hit", 2026) not in tmdb.calls and ("details", 777) in tmdb.calls
    assert ident.state_row is rows[0]  # the row is re-keyed, its domestic history carried


def test_override_tab_matching_the_state_row_needs_no_lookup():
    tmdb = TMDB()
    ident = run(entry(), tmdb, rows=[state_row(5, "New Hit", tn_slug="New-Hit-(2026)")], overrides={"New-Hit-(2026)": 5})
    assert (ident.status, ident.tmdb_id, ident.method) == ("resolved", 5, "override") and tmdb.calls == []


def test_state_row_override_column_rekeys():
    tmdb = TMDB()
    rows = [state_row(1, "New Hit", tn_slug="New-Hit-(2026)", tmdb_id_override="888")]
    ident = run(entry(), tmdb, rows=rows)
    assert (ident.status, ident.tmdb_id, ident.method, ident.confidence) == ("resolved", 888, "state_override", "manual")
    assert tmdb.calls == [("details", 888)]


def test_override_with_failed_details_is_unresolved_not_guessed():
    ident = run(entry(), TMDB(fail_details=True), overrides={"New-Hit-(2026)": 777})
    assert ident.status == "unresolved" and ident.reason.startswith("details_failed")


# ── existing state rows ─────────────────────────────────────────────────

def test_state_row_matched_by_slug_needs_no_lookup():
    tmdb = TMDB()
    row = state_row(5, "Renamed On TMDB", tn_slug="New-Hit-(2026)", identity_method="search", identity_confidence="high")
    ident = run(entry(), tmdb, rows=[row])
    assert (ident.status, ident.tmdb_id, ident.method, ident.confidence) == ("resolved", 5, "search", "high")
    assert tmdb.calls == []


def test_legacy_row_needs_title_and_compatible_year():
    tmdb = TMDB()
    ok = run(entry(), tmdb, rows=[state_row(5, "New Hit", release_date="2026-10-10")])
    assert (ok.status, ok.tmdb_id, ok.method, ok.confidence) == ("resolved", 5, "state_legacy_title_year", "legacy")
    assert tmdb.calls == []


def test_legacy_row_with_same_title_other_year_is_a_different_film():
    # A remake: the state row is the 2018 film; today's chart row is the 2026 one.
    tmdb = TMDB([result(9, "New Hit", "2026-10-12")])
    ident = run(entry(), tmdb, rows=[state_row(5, "New Hit", release_date="2018-10-19")])
    assert (ident.status, ident.tmdb_id, ident.method) == ("resolved", 9, "search")
    assert any("same title, different film" in c.get("rejected", "") for c in ident.considered)


def test_row_with_a_different_slug_is_never_matched_by_title():
    tmdb = TMDB([])
    ident = run(entry(), tmdb, rows=[state_row(5, "New Hit", release_date="2026-10-10", tn_slug="New-Hit-(2026-UK)")])
    assert ident.status == "unresolved" and ident.state_row is None


def test_several_state_rows_for_one_slug_is_unresolved():
    rows = [state_row(5, "New Hit", tn_slug="New-Hit-(2026)"), state_row(6, "New Hit", tn_slug="New-Hit-(2026)")]
    ident = run(entry(), TMDB(), rows=rows)
    assert ident.status == "unresolved" and ident.reason.startswith("ambiguous_state")


def test_legacy_row_without_slug_year_uses_implied_opening():
    e = entry(slug="New-Hit")  # no year in the slug
    assert run(e, TMDB(), rows=[state_row(5, "New Hit", release_date="2026-10-14")]).status == "resolved"
    assert run(e, TMDB([]), rows=[state_row(5, "New Hit", release_date="2026-03-01")]).status == "unresolved"


# ── TMDB search ─────────────────────────────────────────────────────────

def test_search_resolves_unique_film_with_high_confidence():
    tmdb = TMDB([result(9, "New Hit", "2026-10-12")])
    ident = run(entry(), tmdb)
    assert (ident.status, ident.tmdb_id, ident.method, ident.confidence) == ("resolved", 9, "search", "high")
    assert ident.details["imdb_id"] == "tt0000009"
    assert ("search", "New Hit", 2026) in tmdb.calls and ("search", "New Hit", None) in tmdb.calls


def test_remake_is_separated_by_year():
    tmdb = TMDB([result(1, "Superman", "1978-12-15"), result(2, "Superman", "2025-07-11"), result(3, "Superman", "2026-10-12")])
    ident = run(entry("Superman", "Superman-(2026)"), tmdb)
    assert ident.tmdb_id == 3
    rejected = {c.get("tmdb_id"): c.get("rejected", "") for c in ident.considered}
    assert "year 1978" in rejected[1]
    assert "not within 60 days" in rejected[2]  # last year's film is outside the release window


def test_sequel_never_matches_the_original_title():
    tmdb = TMDB([result(1, "Saw", "2004-10-29"), result(2, "Saw X", "2026-10-12")])
    assert run(entry("Saw X", "Saw-X-(2026)"), tmdb).tmdb_id == 2
    only_sequel = TMDB([result(2, "Saw X", "2026-10-12")])
    ident = run(entry("Saw", "Saw-(2026)"), only_sequel)
    assert ident.status == "unresolved" and ident.reason.startswith("no_match")


def test_title_match_alone_is_refused():
    # Exact title, right year, but released months before this run opened (no US date nearby).
    tmdb = TMDB([result(9, "New Hit", "2026-03-06")])
    ident = run(entry(), tmdb)
    assert ident.status == "unresolved" and ident.reason.startswith("no_match")
    assert any("possible re-release" in c.get("rejected", "") for c in ident.considered)
    assert ("release_dates", 9) in tmdb.calls


def test_us_release_date_can_satisfy_the_window():
    # A foreign film: home release in May, US theatrical opening within the window.
    rd = {9: {"results": [{"iso_3166_1": "FR", "release_dates": [{"type": 3, "release_date": "2026-05-20T00:00:00Z"}]},
                          {"iso_3166_1": "US", "release_dates": [{"type": 1, "release_date": "2026-05-15T00:00:00Z"},
                                                                 {"type": 3, "release_date": "2026-10-10T00:00:00Z"}]}]}}
    ident = run(entry("Le Film", "Le-Film-(2026)"), TMDB([result(9, "Le Film", "2026-05-20")], release_dates=rd))
    assert (ident.status, ident.tmdb_id, ident.confidence) == ("resolved", 9, "medium")


def test_original_title_counts_as_exact():
    tmdb = TMDB([result(9, "The English Title", "2026-10-12", original_title="Le Film")])
    assert run(entry("Le Film", "Le-Film-(2026)"), tmdb).tmdb_id == 9


def test_ambiguous_candidates_are_not_guessed():
    tmdb = TMDB([result(1, "New Hit", "2026-10-09"), result(2, "New Hit", "2026-10-16")])
    ident = run(entry(), tmdb)
    assert ident.status == "unresolved" and ident.reason.startswith("ambiguous: 2")
    assert {c["tmdb_id"] for c in ident.considered if "accepted_by" in c} == {1, 2}
    assert not [c for c in tmdb.calls if c[0] == "details"]


def test_candidate_without_imdb_id_is_unresolved():
    tmdb = TMDB([result(9, "New Hit", "2026-10-12")], details={9: {"tmdb_id": 9, "imdb_id": "", "title": "New Hit"}})
    ident = run(entry(), tmdb)
    assert ident.status == "unresolved" and ident.reason.startswith("no_imdb_id")


def test_no_year_evidence_is_unresolved_without_search():
    tmdb = TMDB([result(9, "New Hit", "2026-10-12")])
    ident = run(entry(slug=None, days=0), tmdb)
    assert ident.status == "unresolved" and ident.reason.startswith("no_year_evidence") and tmdb.calls == []


def test_no_slug_but_days_in_release_gives_medium_confidence():
    ident = run(entry(slug=None, days=10), TMDB([result(9, "New Hit", "2026-10-12")]))
    assert (ident.status, ident.confidence) == ("resolved", "medium")


# ── duplicates ──────────────────────────────────────────────────────────

def _ident(tid, row_tid=None):
    return Identity("t", "s", "resolved", tmdb_id=tid, state_row=state_row(row_tid, "t") if row_tid else None)


def test_duplicate_identity_keeps_only_the_continuing_row():
    a, b = _ident(5, row_tid=5), _ident(5)
    dedupe([a, b])
    assert a.status == "resolved" and b.status == "unresolved" and b.reason.startswith("duplicate_identity")


def test_duplicate_new_identities_are_all_unresolved():
    a, b = _ident(5), _ident(5)
    dedupe([a, b])
    assert a.status == b.status == "unresolved"


# ── through run_daily ───────────────────────────────────────────────────

from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.fakes.fake_http import FakeResponse  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, header_msgs, pick_first_candidate, state_after  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402

ALPHA_DETAILS = {"imdb_id": "tt7001001", "title": "Alpha Film", "release_date": "2026-10-12", "genres": [{"name": "Drama"}],
                 "credits": {"crew": [], "cast": []}, "belongs_to_collection": None, "production_companies": [],
                 "origin_country": ["US"]}


def front(http, method, prefix, response):
    """Route ahead of the env fixture's defaults (first matching route wins)."""
    handler = response if callable(response) else (lambda **_: response)
    http._routes.insert(0, (method, prefix, handler))


def search_calls(http, title):
    return [c for c in http.calls_to("https://api.themoviedb.org/3/search/movie") if c["params"]["query"] == title]


@pytest.fixture
def run_env(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    return env


def test_new_film_resolved_by_search_is_tracked_with_identity_columns(run_env):
    http = run_env["http"]
    front(http, "GET", "https://api.themoviedb.org/3/search/movie", FakeResponse(200, {"results": [
        result(7001, "Alpha Film", "2026-10-12"), result(7000, "Alpha Film", "1994-04-01"), result(7002, "Alpha Film 2", "2028-01-01")]}))
    front(http, "GET", "https://api.themoviedb.org/3/movie/7001", FakeResponse(200, ALPHA_DETAILS))
    assert main(run_env) == 0
    rows = {r["tmdb_id"]: r for r in state_after(run_env["gc"])}
    alpha = rows[7001]
    assert (alpha["tn_slug"], alpha["identity_method"], alpha["identity_confidence"]) == ("Alpha-Film-(2026)", "search", "high")
    assert alpha["imdb_id"] == "tt7001001" and 7000 not in rows
    report, _, _ = artifacts(run_env)
    (a,) = [i for i in report["identity"] if i["title"] == "Alpha Film"]
    assert a["status"] == "resolved" and any("year 1994" in c.get("rejected", "") for c in a["considered"])


def test_legacy_state_row_is_backfilled_with_its_slug(run_env):
    assert main(run_env) == 0
    beta = {r["tmdb_id"]: r for r in state_after(run_env["gc"])}[5001]
    assert (beta["tn_slug"], beta["identity_method"], beta["identity_confidence"]) == (
        "Beta-Story-(2026)", "state_legacy_title_year", "legacy")


def test_unresolved_film_is_skipped_reported_and_flagged_in_header(run_env):
    assert main(run_env) == 0  # the default search returns nothing for Alpha Film
    assert 7001 not in {r["tmdb_id"] for r in state_after(run_env["gc"])}
    header = json.dumps(header_msgs(run_env["slack"])[0]["blocks"], ensure_ascii=False)
    assert "Film identity unresolved" in header and "Alpha Film (no_match)" in header and "identity_overrides" in header
    report, _, _ = artifacts(run_env)
    (a,) = [i for i in report["identity"] if i["title"] == "Alpha Film"]
    assert a["status"] == "unresolved" and a["reason"].startswith("no_match")


def test_rerelease_on_the_chart_is_never_searched_or_tracked(run_env):
    # The synthetic chart lists Old-Classic-(1985): $210M total, 12 days (a re-release).
    assert main(run_env) == 0
    assert search_calls(run_env["http"], "Old Classic") == []
    report, _, _ = artifacts(run_env)
    (oc,) = [i for i in report["identity"] if i["title"] == "Old Classic"]
    assert oc["status"] == "rerelease"
    header = json.dumps(header_msgs(run_env["slack"])[0]["blocks"], ensure_ascii=False)
    assert "Old Classic" not in header  # expected, so not an editor warning


def test_identity_overrides_tab_resolves_an_unresolved_film(run_env):
    run_env["gc"].book("S").add_tab("identity_overrides", [["tn_slug", "tmdb_id", "note"],
                                                           ["Alpha-Film-(2026)", "7001", "checked by editor"],
                                                           ["Bad-Row", "not-a-number", ""]])
    front(run_env["http"], "GET", "https://api.themoviedb.org/3/movie/7001", FakeResponse(200, ALPHA_DETAILS))
    assert main(run_env) == 0
    alpha = {r["tmdb_id"]: r for r in state_after(run_env["gc"])}[7001]
    assert (alpha["identity_method"], alpha["identity_confidence"]) == ("override", "manual")
    assert search_calls(run_env["http"], "Alpha Film") == []
    assert not [c for c in run_env["gc"].writes() if c[0] == "identity_overrides"]  # read-only to Faramir


def test_state_override_rekeys_the_row_and_drops_the_wrong_identity(run_env):
    from faramir.sheet import STATE_HEADERS

    ws = run_env["gc"].book("S").worksheet("state")
    ws.rows[1][list(STATE_HEADERS).index("tmdb_id_override")] = "5002"  # editor correction
    details = dict(ALPHA_DETAILS, imdb_id="tt5002002", title="Beta Story", release_date="2026-09-18")
    front(run_env["http"], "GET", "https://api.themoviedb.org/3/movie/5002", FakeResponse(200, details))
    assert main(run_env) == 0
    rows = {r["tmdb_id"]: r for r in state_after(run_env["gc"])}
    assert 5001 not in rows
    beta = rows[5002]
    assert beta["identity_method"] == "state_override" and beta["tmdb_id_override"] == 5002
    assert beta["domestic_yesterday"] == 140_000_000 and beta["domestic_today"] == 152_000_000  # chart history carried
    assert beta["imdb_id"] == "tt5002002"


# ── legacy 17-column state tab ──────────────────────────────────────────

def test_legacy_state_tab_is_refused_by_preflight_then_upgraded_by_migration(run_env):
    # Increment 12 replaces Increment 9's "upgraded on the first ordinary write": an ordinary run
    # must never migrate production data. It refuses a legacy tab; scripts/migrate_sheets.py
    # upgrades it through the state protocol (backup, write, verify); then runs proceed.
    import importlib.util
    import os

    from faramir.sheet import LEGACY_STATE_HEADERS, STATE_HEADERS
    from faramir.state_integrity import LKG_TAB, META_TAB, STATE_TAB, initial_meta, meta_values, parse_meta, row_values
    from faramir.state_store import StateStore

    book = run_env["gc"].book("S")
    legacy = [list(LEGACY_STATE_HEADERS)] + row_values(list(LEGACY_STATE_HEADERS), [state_row(5001, "Beta Story", imdb_id="tt5001001")])
    for tab in (STATE_TAB, LKG_TAB):
        book.worksheet(tab).rows = [list(r) for r in legacy]
    book.worksheet(META_TAB).rows = meta_values(initial_meta(legacy, "2026-10-21T15:00:00+00:00", "init"))
    assert StateStore(run_env["gc"], "S", "probe").open().action == "PROCEED"  # still a valid state

    assert main(run_env) == 3
    assert run_env["gc"].writes() == [] and book.worksheet(STATE_TAB).data == [[str(c) for c in r] for r in legacy]
    alert = json.dumps(run_env["slack"].webhook_posts[0]["payload"])
    assert "legacy 17-column layout" in alert and "migrate_sheets.py" in alert

    spec = importlib.util.spec_from_file_location("migrate_sheets", os.path.join(
        os.path.dirname(__file__), "..", "scripts", "migrate_sheets.py"))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    from faramir import runtime

    with runtime.use(runtime.RunContext(mode=runtime.LIVE, enabled=True, run_id="migration",
                                        out_dir=str(run_env["tmp"] / "migration"))):
        mig.apply(run_env["gc"], "S", "migration")
    assert book.worksheet(STATE_TAB).data[0] == list(STATE_HEADERS)
    lkg = book.worksheet(LKG_TAB).data
    assert lkg[0] == list(STATE_HEADERS) and lkg[1][:17] == [str(c) for c in legacy[1]] and lkg[1][17:] == ["", "", ""]
    assert parse_meta(book.worksheet(META_TAB).data)["state"]["status"] == "ok"

    for p in list((run_env["tmp"] / "out").iterdir()):
        __import__("shutil").rmtree(p)
    assert main(run_env) == 0  # the migrated workbook passes preflight


def test_restore_from_a_legacy_layout_backup_upgrades_the_rows():
    from faramir.sheet import LEGACY_STATE_HEADERS, STATE_HEADERS
    from faramir.state_integrity import (LKG_TAB, META_TAB, RESTORE, STATE_TAB, initial_meta, meta_values,
                                         parse_meta, row_values)
    from faramir.state_store import StateStore
    from tests.fakes.fake_gspread import FakeClient

    gc = FakeClient()
    book = gc.book("S")
    L = list(LEGACY_STATE_HEADERS)
    legacy = [L] + row_values(L, [state_row(1, "One", tmdb_id_override="123"), state_row(2, "Two")])
    partial = [list(r) for r in legacy]
    partial[2][L.index("domestic_today")] = "999"                        # an interrupted restore left it half-written
    book.add_tab(STATE_TAB, partial)                                     # editor override "123" is still live
    book.add_tab(LKG_TAB, [list(r) for r in legacy])
    m = initial_meta(legacy, "2026-10-21T15:00:00+00:00", "init")
    m["state"]["status"] = "restoring"
    book.add_tab(META_TAB, meta_values(m))

    s = StateStore(gc, "S", "recovery")
    assert s.open().action == RESTORE
    s.acquire_lock()
    s.recover()
    s.release_lock()
    data = book.worksheet(STATE_TAB).data
    assert data[0] == list(STATE_HEADERS) and len(data) == 3
    assert [r[:17] for r in data[1:]] == [[str(c) for c in r] for r in legacy[1:]]  # content identical, override kept
    assert parse_meta(book.worksheet(META_TAB).data)["state"]["status"] == "restored"
    assert StateStore(gc, "S", "next").open().action == "PROCEED"
