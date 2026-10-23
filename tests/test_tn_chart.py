"""Increment 10: The Numbers chart parser — classification, completeness,
re-release evidence. Synthetic HTML only; no network, no collection."""

import os
from datetime import date

import pytest

from faramir.identity import classify_engagement, parse_slug
from faramir.tn_chart import daily_url, parse_chart, weekend_friday, weekend_url
from tests.fixtures.tn_synth import DAILY_HEADERS, WEEKEND_FILMS, WEEKEND_HEADERS, page

FRI = date(2026, 10, 16)
WED = date(2026, 10, 21)
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def weekend(html=None, **kw):
    return parse_chart(html if html is not None else page(**kw), weekend_url(FRI), "weekend", FRI)


def codes(chart):
    return {i.code for i in chart.issues}


def films_with(index, **changes):
    films = [list(f) for f in WEEKEND_FILMS]
    keys = ["rank", "prev", "title", "slug", "gross", "change", "theaters", "total", "days"]
    for k, v in changes.items():
        films[index][keys.index(k)] = v
    return [tuple(f) for f in films]


# ── URLs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("day, friday", [
    (date(2026, 10, 16), FRI), (date(2026, 10, 17), FRI), (date(2026, 10, 18), FRI),   # Fri, Sat, Sun
    (date(2026, 10, 19), FRI), (date(2026, 10, 22), FRI),                              # Mon, Thu -> previous Friday
    (date(2026, 10, 23), date(2026, 10, 23)), (date(2027, 1, 2), date(2027, 1, 1)),    # next Friday; year boundary
])
def test_weekend_friday(day, friday):
    assert weekend_friday(day) == friday


def test_weekend_url_is_friday_dated():
    assert weekend_url(FRI) == "https://www.the-numbers.com/box-office-chart/weekend/2026/10/16"
    with pytest.raises(ValueError):
        weekend_url(date(2026, 10, 18))  # the Sunday URL 404s on TN
    assert daily_url(WED) == "https://www.the-numbers.com/box-office-chart/daily/2026/10/21"


# ── classification ──────────────────────────────────────────────────────

def test_committed_weekend_fixture_parses_exactly():
    with open(os.path.join(FIXTURES, "tn_weekend_synthetic.html"), encoding="utf-8") as f:
        c = weekend(f.read())
    assert (c.status, c.verified, c.chart_type) == ("complete", True, "weekend")
    assert (c.reporting_date, c.weekend_start, c.weekend_end) == ("2026-10-16", "2026-10-16", "2026-10-18")
    assert (c.reported_count, c.parsed_count, c.reported_total, c.parsed_total) == (8, 8, 56_025_000, 56_025_000)
    assert c.source_url.endswith("/weekend/2026/10/16") and c.issues == []
    r = c.rows[0]
    assert (r.title_reported, r.tn_slug, r.slug_year) == ("Iron Harbor", "Iron-Harbor-(2026)", 2026)
    assert (r.source_rank, r.derived_rank, r.prev_status, r.prev_rank_reported) == (1, 1, "new", None)
    assert (r.gross, r.theaters, r.theater_avg, r.total_gross, r.days_in_release) == (31_250_000, 3_600, 8_681, 31_250_000, 3)
    assert r.weekly_change_pct is None and r.daily_change_pct is None  # blank for a new film: unavailable, not 0
    q = c.rows[1]
    assert (q.prev_status, q.prev_rank_reported, q.weekly_change_pct) == ("ranked", 1, -52.0)


def test_committed_daily_fixture_parses_as_daily():
    with open(os.path.join(FIXTURES, "tn_daily_parser_synthetic.html"), encoding="utf-8") as f:
        c = parse_chart(f.read(), daily_url(WED), "daily", WED)
    assert (c.status, c.chart_type, c.reporting_date, c.weekend_start) == ("complete", "daily", "2026-10-21", None)
    assert c.rows[1].daily_change_pct == -52.0


def test_daily_page_is_never_accepted_as_weekend_data():
    c = parse_chart(page("daily", WED), weekend_url(FRI), "weekend", FRI)
    assert c.status == "wrong_chart" and not c.verified and c.chart_type == "daily"
    assert {"wrong_chart_type", "wrong_date"} <= codes(c)


def test_weekend_page_is_not_a_daily_chart():
    c = parse_chart(page(), daily_url(WED), "daily", WED)
    assert c.status == "wrong_chart" and not c.verified


def test_other_weekend_date_is_wrong_chart():
    c = weekend(page(day=date(2026, 10, 9)))
    assert c.status == "wrong_chart" and "wrong_date" in codes(c)


def test_unrecognized_heading_is_malformed():
    c = weekend(heading="Box Office Results")
    assert c.status == "malformed" and "heading_unrecognized" in codes(c)


def test_weekend_heading_with_daily_columns_is_malformed():
    c = weekend(headers=DAILY_HEADERS)
    assert c.status == "malformed" and "unexpected_column" in codes(c)


def test_weekend_heading_date_must_be_a_friday():
    c = parse_chart(page(day=date(2026, 10, 18)), "u", "weekend")
    assert c.status == "malformed" and "weekend_not_friday" in codes(c)


# ── header changes ──────────────────────────────────────────────────────

def test_reordered_columns_map_by_header():
    order = [2, 0, 1, 7, 3, 5, 4, 8, 6]
    hdr = [WEEKEND_HEADERS[i] for i in order]
    base = weekend()
    moved = weekend(headers=hdr)  # the builder fills cells by header name, so columns move with headers
    assert moved.status == "complete"
    strip = lambda rows: [{k: v for k, v in vars(r).items() if k != "raw"} for r in rows]  # noqa: E731
    assert strip(moved.rows) == strip(base.rows)


@pytest.mark.parametrize("change, code", [
    (lambda h: [x if x != "Gross" else "Weekend Gross" for x in h], "unknown_column"),
    (lambda h: [x for x in h if x != "Theaters"], "missing_column"),
    (lambda h: h + ["Theaters"], "duplicate_column"),
])
def test_header_changes_are_malformed(change, code):
    hdr = change(list(WEEKEND_HEADERS))
    c = weekend(headers=hdr, row_hook=lambda f, cells: (cells + [""] * len(hdr))[:len(hdr)])
    assert c.status == "malformed" and code in codes(c) and not c.verified


def test_unmarked_table_is_used_only_when_it_is_the_only_table():
    assert weekend(desktop_class=False).status == "malformed"   # desktop + mobile, neither marked: no guessing


# ── missing and malformed cells: never zero, never invented ─────────────

def test_missing_gross_is_none_and_partial():
    c = weekend(films=films_with(3, gross=""), footer_total=56_025_000)
    r = c.rows[3]
    assert r.gross is None and r.derived_rank is None and c.parsed_total is None
    assert c.status == "partial" and "missing_value" in codes(c)


@pytest.mark.parametrize("field, value", [("theaters", "n/a"), ("total", "$1.2M"), ("days", "3d"), ("gross", "$12,40")])
def test_malformed_numbers_are_none_and_malformed(field, value):
    c = weekend(films=films_with(1, **{field: value}), footer_total=56_025_000)
    assert c.status == "malformed" and "malformed_value" in codes(c)
    attr = {"theaters": "theaters", "total": "total_gross", "days": "days_in_release", "gross": "gross"}[field]
    assert getattr(c.rows[1], attr) is None


def test_missing_theaters_is_partial_not_zero():
    c = weekend(films=films_with(7, theaters=""))
    assert c.rows[7].theaters is None and c.rows[7].theater_avg is None and c.status == "partial"


def test_malformed_change_is_flagged():
    c = weekend(films=films_with(1, change="n/c"))
    assert c.rows[1].weekly_change_pct is None and c.status == "malformed"


def test_missing_title_link_is_partial():
    c = weekend(films=films_with(2, slug=""))
    assert c.rows[2].tn_slug == "" and c.status == "partial"


def test_total_below_gross_is_inconsistent():
    c = weekend(films=films_with(1, total=1_000_000))
    assert "inconsistent_value" in codes(c) and c.status == "malformed"


# ── ranks: reported vs derived, valid sequences ─────────────────────────

def test_reported_and_derived_ranks_are_distinct():
    c = weekend()
    assert [r.source_rank for r in c.rows] == [1, 2, 3, 4, 5, None, None, None]
    assert [r.derived_rank for r in c.rows] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [r.source_rank_text for r in c.rows][5:] == ["-", "-", "-"]


@pytest.mark.parametrize("index, rank, code", [
    (1, "2nd", "rank_malformed"), (1, "", "rank_malformed"), (1, "two", "rank_malformed"),
    (2, "4", "rank_sequence"),     # 1, 2, 4
    (6, "6", "rank_sequence"),     # a number after "-"
    (0, "2", "rank_sequence"),     # does not start at 1
])
def test_invalid_ranks(index, rank, code):
    c = weekend(films=films_with(index, rank=rank))
    assert c.status == "malformed" and code in codes(c) and not c.verified


def test_printed_ranks_must_follow_gross_order():
    films = films_with(1, gross=40_000_000, total=86_500_000)
    c = weekend(films=films)
    assert "rank_order" in codes(c) and c.status == "malformed"


def test_tied_grosses_share_a_derived_rank():
    films = films_with(6, gross=210_000)  # ties the unranked row above it
    c = weekend(films=films)
    assert [r.derived_rank for r in c.rows][5:7] == [6, 6] and c.rows[7].derived_rank == 8


# ── footer and reconciliation ───────────────────────────────────────────

def test_wrong_footer_total_is_malformed():
    c = weekend(footer_total=56_025_001)
    assert c.status == "malformed" and "gross_mismatch" in codes(c) and not c.verified


def test_footer_count_higher_than_rows_is_partial():
    c = weekend(footer_count=9)
    assert c.status == "partial" and "rows_missing" in codes(c)


def test_footer_count_lower_than_rows_is_malformed():
    c = weekend(footer_count=7)
    assert c.status == "malformed" and "rows_extra" in codes(c)


def test_missing_footer_cannot_be_verified():
    c = weekend(footer=False)
    assert c.status == "partial" and "footer_missing" in codes(c) and c.reported_total is None


def test_truncated_chart_is_partial_never_verified():
    # The page lost its last three rows but kept the footer.
    films = WEEKEND_FILMS[:5]
    c = weekend(films=films, footer_count=8, footer_total=56_025_000)
    assert c.status == "partial" and not c.verified
    assert {"rows_missing", "gross_short"} <= codes(c) and "gross_mismatch" not in codes(c)


def test_missing_rows_with_too_large_a_sum_is_malformed():
    c = weekend(films=WEEKEND_FILMS[:5], footer_count=8, footer_total=50_000_000)
    assert c.status == "malformed" and "gross_mismatch" in codes(c)


def test_no_table_or_empty_page():
    assert weekend("<html><body><h1>Weekend Domestic Box Office October 16, 2026</h1></body></html>").status == "malformed"
    assert weekend("").status == "malformed"


# ── re-release evidence ─────────────────────────────────────────────────

def test_engagement_classification_in_chart():
    c = weekend()
    by_title = {r.title_reported: r for r in c.rows}
    galaxy = by_title["Galaxy Run"]  # 2019 film, 2,733 days, lifetime $640M
    assert (galaxy.engagement, galaxy.first_us_release, galaxy.engagement_start, galaxy.total_gross_scope) == (
        "rerelease", "2019-04-26", None, "all_engagements")
    assert galaxy.tn_slug == "Galaxy-Run-(2019)"  # the original film's identity is kept
    voyage = by_title["Le Voyage"]   # 2024 French film, first US run at 59 days
    assert (voyage.engagement, voyage.engagement_start, voyage.total_gross_scope) == ("late_release", "2026-08-21", "this_engagement")
    assert by_title["Old Reel"].engagement == "rerelease"
    assert by_title["Night Ferry"].slug_year == 2026 and by_title["Night Ferry"].engagement == "original"


@pytest.mark.parametrize("slug_year, days, kind", [
    (2026, 10, "original"), (2025, 80, "original"),          # December release still running in 2026
    (2019, 2733, "rerelease"), (1985, 15086, "rerelease"),
    (2024, 59, "late_release"), (None, 20, "unknown"), (None, 400, "rerelease"),
    (1985, None, "rerelease"), (2026, None, "original"), (None, None, "unknown"),
])
def test_classify_engagement(slug_year, days, kind):
    assert classify_engagement(slug_year, days, date(2026, 10, 18)) == kind


@pytest.mark.parametrize("href, year", [
    ("/movie/Devils-The-(1971-United-Kingdom)", 1971),
    ("/movie/Tazza-The-Song-of-Beelzebub-(2026-South-Korea)", 2026),
    ("/movie/Night-Ferry-(2026-United-Kingdom)#tab=summary", 2026),
])
def test_slug_year_with_multi_word_country(href, year):
    # Regression: Increment 9's slug pattern allowed only one word after the year.
    assert parse_slug(href)[1] == year


# ── content hash (Increment 11's collection key) ────────────────────────

def test_content_hash_depends_on_content_only():
    a, b = weekend(), weekend()
    assert a.content_hash() == b.content_hash()
    assert weekend(films=films_with(7, gross=21_000), footer_total=56_026_000).content_hash() != a.content_hash()
    moved = parse_chart(page(), "https://elsewhere", "weekend", FRI)
    assert moved.content_hash() == a.content_hash()  # not the URL or the parse time


def test_parser_module_does_not_fetch():
    import faramir.tn_chart as m

    src = open(m.__file__, encoding="utf-8").read()
    assert "requests" not in src and "urlopen" not in src


# ── identity confidence is never upgraded by a chart match ──────────────

from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, pick_first_candidate, state_after  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_legacy_identity_stays_legacy_across_runs(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    for n in range(2):
        FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
        assert main(env) == 0
        beta = {r["tmdb_id"]: r for r in state_after(env["gc"])}[5001]
        report, _, _ = artifacts(env)
        (b,) = [i for i in report["identity"] if i["title"] == "Beta Story"]
        # Run 1 matches by title+year and backfills the slug; run 2 matches by that slug. Neither upgrades it.
        assert beta["identity_confidence"] == "legacy" and b["identity_class"] == "legacy"
        assert b["method"] == "state_legacy_title_year"
        for p in list((env["tmp"] / "out").iterdir()):
            for f in p.iterdir():
                f.unlink()
            p.rmdir()


def test_rerelease_identity_keeps_engagement_evidence(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    (oc,) = [i for i in report["identity"] if i["title"] == "Old Classic"]
    assert oc["identity_class"] == "rerelease"
    assert oc["engagement"]["original_identity"] == "Old-Classic-(1985)" and oc["engagement"]["slug_year"] == 1985


# ── opt-in: the real captured pages (never committed) ───────────────────

CAPTURE_DIR = os.environ.get("FARAMIR_TN_CAPTURE_DIR", "")


@pytest.mark.local_capture
def test_captured_weekend_2026_10_16():
    with open(os.path.join(CAPTURE_DIR, "weekend_2026_10_16.html"), encoding="utf-8") as f:
        c = parse_chart(f.read(), weekend_url(FRI), "weekend", FRI)
    assert c.status == "complete" and c.issues == []
    r = c.rows[0]
    assert (r.title_reported, r.gross, r.theaters) == ("Street Fighter", 30_775_377, 3_575)
    assert (c.reported_count, c.reported_total, c.parsed_total) == (62, 103_334_297, 103_334_297)
    assert [x.source_rank for x in c.rows[:15]] == list(range(1, 16)) and c.rows[15].source_rank is None


@pytest.mark.local_capture
def test_captured_daily_2026_10_21():
    with open(os.path.join(CAPTURE_DIR, "daily_2026_10_21.html"), encoding="utf-8") as f:
        html = f.read()
    c = parse_chart(html, daily_url(WED), "daily", WED)
    assert c.status == "complete" and (c.reported_count, c.parsed_total) == (26, 9_470_343)
    assert parse_chart(html, weekend_url(FRI), "weekend", FRI).status == "wrong_chart"


def test_monday_merge_is_labelled_daily_not_weekend(env):
    from tests.fakes.fake_http import FakeResponse
    from tests.test_run_daily_corpus import load_run_daily

    class Monday(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 26)

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    env["http"].route("GET", "https://www.the-numbers.com/box-office-chart/weekend/", FakeResponse(404, text=""))
    mod = load_run_daily()
    mod.date = Monday
    assert mod.main(["--out-root", str(env["tmp"] / "out")]) == 0
    report, _, _ = artifacts(env)
    assert report["monday_merge"] == {"source": "sunday_daily_chart_via_legacy_fetch_weekend_list",
                                      "is_weekend_data": False, "entries": report["monday_merge"]["entries"]}
    weekend_calls = env["http"].calls_to("https://www.the-numbers.com/box-office-chart/weekend/")
    assert [c["url"][-10:] for c in weekend_calls] == ["2026/10/25"]  # legacy behavior unchanged (Sunday URL)


def test_long_running_rereleases_are_recorded_in_the_report_only(env):
    from tests.fakes.fake_http import FakeResponse

    with open(os.path.join(FIXTURES, "tn_daily_synthetic.html"), encoding="utf-8") as f:
        html = f.read().replace(
            '<tr><td>-</td><td>(7)</td>',
            '<tr><td>-</td><td>(8)</td><td><a href="/movie/Galaxy-Run-(2019)#tab=summary">Galaxy Run</a></td>'
            '<td>$13,456</td><td>-2%</td><td></td><td>320</td><td>$42</td><td>$898,435,060</td><td>2,736</td></tr>'
            '<tr><td>-</td><td>(7)</td>')
    env["http"]._routes.insert(0, ("GET", "https://www.the-numbers.com/box-office-chart/daily/",
                                   lambda **_: FakeResponse(200, text=html)))
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    (g,) = report["rerelease_engagements"]
    assert (g["tn_slug"], g["days_in_release"], g["total_gross_scope"]) == ("Galaxy-Run-(2019)", 2736, "all_engagements")
    assert "Galaxy Run" not in {i["title"] for i in report["identity"]}           # never resolved or picked
    assert 2019 not in {r.get("release_year") for r in state_after(env["gc"])}     # never tracked
