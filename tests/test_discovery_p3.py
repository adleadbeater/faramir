"""Phase 3: Discovery generators, claim verification, open pass, exclusion, novelty, runner;
editorial assignment and published-article matching; reports reproduced from the ledger."""

from datetime import date, datetime, timedelta, timezone

import pytest

from faramir.discovery import generators as G, novelty, open_pass, runner
from faramir.discovery.items import Claim, DiscoveryItem, verify, verify_claim
from faramir.events.ledger import Ledger
from faramir.strategies.weekend import Weekend, WeekendRow

FRI = date(2026, 10, 16)


def R(key, rank, gross, theaters, days, prev="(1)", engagement="original", tmdb=None):
    return WeekendRow(key, key.replace("-", " "), rank, rank, gross, theaters, gross * 3, days, prev, engagement,
                      tmdb, None, "unmatched")


def W(fri, rows, coll=None):
    return Weekend(fri.isoformat(), coll or f"c{fri:%m%d}", "provisional", "https://tn/weekend", rows)


def filler(n, start_rank=4, gross=1_000_000, theaters=1000, days=17):
    return [R(f"Filler-{i}", start_rank + i, gross - i * 10_000, theaters, days) for i in range(n)]


# ── generators ──────────────────────────────────────────────────────────

def test_comeback_after_two_weekends_outside_the_top_ten():
    f = FRI - timedelta(weeks=3)
    weeks = [W(f, [R("Back", 5, 3e6, 2000, 10)] + filler(9, 1)),
             W(f + timedelta(weeks=1), filler(10, 1) + [R("Back", 14, 5e5, 600, 17)]),
             W(f + timedelta(weeks=2), filler(10, 1) + [R("Back", 18, 3e5, 400, 24)]),
             W(FRI, [R("Back", 7, 2e6, 1500, 31)] + filler(9, 1))]
    (item,) = G.comeback(weeks)
    assert item.film_key == "Back" and item.value == 7 and verify(item, {w.weekend_start: w for w in weeks}).status == "proposed"
    assert G.comeback(weeks[:3]) == []  # needs the full window


def test_rerelease_share_and_its_verification():
    w = W(FRI, [R("New", 1, 9e6, 3000, 3), R("Classic", 2, 3e6, 800, 20000, engagement="rerelease"),
                R("Other", 3, 1e6, 900, 17)])
    (item,) = G.rerelease_share(w)
    assert item.value == 23.1 and item.metric == "rerelease_share_pct"
    assert verify(item, {w.weekend_start: w}).status == "proposed"
    assert G.rerelease_share(W(FRI, [R("New", 1, 9e6, 3000, 3)])) == []


def test_per_theater_outlier_outside_the_top_three():
    rows = filler(9, 1, gross=2_000_000, theaters=2000) + [R("Platform", 12, 900_000, 60, 3)]
    (item,) = G.per_theater_outlier(W(FRI, rows))
    assert item.film_key == "Platform" and item.value == 15000 and item.baseline["multiple"] == 15.3


def test_weekend_change_zscore_needs_a_cohort():
    prev_rows = [R(f"F{i}", i + 1, 10e6, 3000, 10) for i in range(10)]
    cur_rows = [R(f"F{i}", i + 1, 5e6 + i * 1e4, 3000, 17) for i in range(9)] + [R("F9", 10, 9.5e6, 3000, 17)]
    items = G.weekend_change_zscore([W(FRI - timedelta(weeks=1), prev_rows), W(FRI, cur_rows)])
    assert [i.film_key for i in items] == ["F9"] and items[0].baseline["z"] > 2 and "strong_hold" in items[0].pattern_tags
    assert G.weekend_change_zscore([W(FRI - timedelta(weeks=1), prev_rows[:5]), W(FRI, cur_rows[:5])]) == []


def test_expansion_surge():
    weeks = [W(FRI - timedelta(weeks=1), [R("Platform", 9, 1e6, 600, 10)]), W(FRI, [R("Platform", 4, 3e6, 2100, 17)])]
    (item,) = G.expansion_surge(weeks)
    assert item.value == 250.0 and len(item.claims) == 4


def test_format_contrast_needs_metadata():
    w = W(FRI, [R("Concert", 3, 8e6, 2500, 3, tmdb=77)] + filler(3, 4))
    assert G.format_contrast(w, {}) == []
    (item,) = G.format_contrast(w, {77: {"genres": ["Music", "Documentary"], "origin_country": "US"}})
    assert item.baseline["format"] == "concert/music"


# ── verification: an unverified anomaly is never presented ──────────────

def test_claims_must_match_stored_rows():
    w = W(FRI, [R("A", 1, 9e6, 3000, 3)])
    ws = {w.weekend_start: w}
    ok = Claim("A", "weekend_gross", 9e6, w.weekend_start, f"{w.collection_id}:A")
    assert verify_claim(ok, ws) is None
    assert "claim says" in verify_claim(Claim("A", "weekend_gross", 9.1e6, w.weekend_start, f"{w.collection_id}:A"), ws)
    assert "not on the" in verify_claim(Claim("Z", "weekend_gross", 1, w.weekend_start, f"{w.collection_id}:Z"), ws)
    assert "no committed weekend" in verify_claim(Claim("A", "weekend_gross", 9e6, "2026-01-02", "x"), ws)
    assert "source row" in verify_claim(Claim("A", "weekend_gross", 9e6, w.weekend_start, "other:A"), ws)
    assert "derived" in verify_claim(Claim("A", "weekend_change_pct", 5, w.weekend_start, f"{w.collection_id}:A"), ws)


def test_item_with_any_bad_claim_is_dropped():
    w = W(FRI, [R("A", 1, 9e6, 3000, 3)])
    item = DiscoveryItem("x", w.weekend_start, "A", "A", "weekend_gross", 9e6, {},
                         [Claim("A", "weekend_gross", 9e6, w.weekend_start, f"{w.collection_id}:A"),
                          Claim("A", "theaters", 4000, w.weekend_start, f"{w.collection_id}:A")], "", "", "", [])
    assert verify(item, {w.weekend_start: w}).status == "dropped_unverified"
    assert verify(DiscoveryItem("x", "p", None, None, "m", 0, {}, [], "", "", "", []), {}).status == "dropped_unverified"


class FakeProposer:
    name = "fake"

    def __init__(self, coll):
        self.coll = coll

    def propose(self, summary):
        p = summary["weekend_start"]
        return [
            {"pattern": "true", "metric": "weekend_gross", "film_key": "A", "title": "A", "value": 9e6,
             "claims": [{"film_key": "A", "metric": "weekend_gross", "value": 9e6, "period": p, "source_row": f"{self.coll}:A"}],
             "why_it_matters": "w", "headline": "h", "alternative": "a", "tags": ["t"]},
            {"pattern": "invented", "metric": "weekend_gross", "film_key": "A", "value": 50e6,
             "claims": [{"film_key": "A", "metric": "weekend_gross", "value": 50e6, "period": p, "source_row": f"{self.coll}:A"}]},
            {"pattern": "no claims at all", "metric": "x", "claims": []},
            {"pattern": "malformed", "claims": [{"metric": "weekend_gross"}]},
        ]


def test_open_pass_keeps_only_verified_proposals():
    w = W(FRI, [R("A", 1, 9e6, 3000, 3)])
    kept, dropped = open_pass.run(FakeProposer(w.collection_id), {"weekend_start": w.weekend_start}, {w.weekend_start: w})
    assert [k.baseline["proposed_by"] for k in kept] == ["fake"]
    assert {d["pattern"] for d in dropped} == {"invented", "no claims at all", "malformed"}
    assert open_pass.run(open_pass.NullOpenPass(), {"weekend_start": "x"}, {}) == ([], [])


# ── exclusion, novelty, runner ──────────────────────────────────────────

class _Ev:
    def __init__(self, slug, period, strategy):
        self.film, self.period, self.strategy = {"tn_slug": slug}, period, strategy


def test_anomaly_explained_by_a_strategy_event_is_excluded():
    rows = [R("Back", 7, 2e6, 1500, 31)] + filler(9, 1)
    f = FRI - timedelta(weeks=3)
    weeks = [W(f, [R("Back", 5, 3e6, 2000, 10)] + filler(9, 1)),
             W(f + timedelta(weeks=1), filler(10, 1)), W(f + timedelta(weeks=2), filler(10, 1)), W(FRI, rows)]
    assert runner.discover(weeks).items
    res = runner.discover(weeks, [_Ev("Back", FRI.isoformat(), "streak")])
    assert not [i for i in res.items if i.generator == "comeback"] and res.excluded[0]["explained_by"] == ["streak"]


def test_similar_prior_and_novelty():
    item = DiscoveryItem("x", "p", "A", "A", "theater_avg", 1, {}, [], "", "", "", ["per_theater_average", "limited_release"])
    prior = [{"id": "old", "pattern_tags": ["per_theater_average", "limited_release"], "metric": "theater_avg", "period": "q"},
             {"id": "other", "pattern_tags": ["comeback"], "metric": "source_rank"}]
    novelty.link(item, prior)
    assert [p["id"] for p in item.similar_prior] == ["old"] and item.novelty == 0.0


def test_runner_reads_history_and_ledgers(monkeypatch, tmp_path):
    from faramir import history as H
    from faramir.tn_chart import parse_chart, weekend_url
    from tests.fakes.fake_gspread import FakeClient
    from tests.fixtures.tn_synth import page

    monkeypatch.setenv("FARAMIR_COLLECT_HISTORY", "true")
    gc = FakeClient()
    gc.book("S").add_tab(H.HISTORY_TAB, [H.HISTORY_HEADERS])
    gc.book("S").add_tab(H.COLLECTIONS_TAB, [H.COLLECTION_HEADERS])
    fri = date(2026, 10, 16)
    chart = parse_chart(page(), weekend_url(fri), "weekend", fri)   # contains a 2019 re-release ($1.15M)
    at = datetime(2026, 10, 18, 14, tzinfo=timezone.utc)
    H.collect(gc, "S", chart, run_id="r", retrieved_at=at, now=at)
    led = Ledger(str(tmp_path))
    res = runner.run(gc, "S", fri.isoformat(), led, collection_enabled=True)
    assert res.status == "ok" and all(i.status == "proposed" for i in res.items)
    assert len(led.read("discovery")) == len(res.items)
    assert all(r["label"].startswith("proposed pattern") for r in led.read("discovery"))
    assert runner.run(gc, "S", "2026-11-20", led, collection_enabled=True).status == "history_unavailable"
    monkeypatch.delenv("FARAMIR_COLLECT_HISTORY")
    assert runner.run(gc, "S", fri.isoformat()).status == "history_collection_disabled"


# ── editorial: assignment and published-article matching ────────────────

from faramir.analytics import cerebro_snapshot as CS  # noqa: E402
from faramir.editorial import assignee, match_published  # noqa: E402
from tests.fixtures import cerebro_synth as SYN  # noqa: E402


def test_assignee_from_thread():
    assert assignee("Assigned to Dana Smith, due 3pm") == "Dana Smith"
    assert assignee("assigning it to @Rob") == "Rob"
    assert assignee("assigned to @dana for tomorrow") == "dana"
    assert assignee("assigned to Dana for tomorrow") == "Dana"
    assert assignee("nice one") is None


def _articles(tmp_path, rows):
    return CS.load(SYN.write(tmp_path / "c.csv", rows), as_of=SYN.AS_OF).articles


def test_published_matching(tmp_path):
    def art(title, days_after, ct="News", url=None):
        d = (date(2026, 10, 10) + timedelta(days=days_after))
        return {**SYN.row(title, 5000, content_type=ct, url=url), "pub_date": f"{d} 09:00:00"}

    arts = _articles(tmp_path, [
        art("New Hit Passes Old Classic at the Box Office", 1, url="https://movieweb.com/match/"),
        art("New Hit Review: A Triumph", 2, ct="Review", url="https://movieweb.com/review/"),
        art("New Hit Passes Old Classic Again", 20, url="https://movieweb.com/too-late/"),
        art("Other Film Box Office", 1, url="https://movieweb.com/other/"),
    ])
    base = {"delivery_state": "posted", "run_date": "2026-10-10", "active_title": "New Hit", "comp_title": "Old Classic",
            "headline": "New Hit Passes Old Classic Domestically"}
    rows = [{**base, "suggestion_id": "s1"},
            {**base, "suggestion_id": "s2", "thread_feedback": "live: https://movieweb.com/explicit/"},
            {**base, "suggestion_id": "s3", "active_title": "Nobody"},
            {**base, "suggestion_id": "s4", "delivery_state": "planned"}]
    got = {m["suggestion_id"]: m for m in match_published(rows, arts)}
    assert got["s1"]["url"] == "https://movieweb.com/match/" and got["s1"]["method"] == "cerebro_title_match"
    assert got["s2"]["url"] == "https://movieweb.com/explicit/" and got["s2"]["method"] == "thread_url"
    assert "s3" not in got and "s4" not in got


def test_ambiguous_matches_are_not_linked(tmp_path):
    def art(title, url):
        return {**SYN.row(title, 5000, url=url), "pub_date": "2026-10-11 09:00:00"}

    arts = _articles(tmp_path, [art("New Hit at the Box Office", "https://movieweb.com/a/"),
                                art("New Hit Box Office Update", "https://movieweb.com/b/")])
    (m,) = match_published([{"suggestion_id": "s", "delivery_state": "posted", "run_date": "2026-10-10",
                             "active_title": "New Hit", "headline": "New Hit Nears $100M"}], arts)
    assert m["ambiguous"] and m["url"] is None and len(m["candidates"]) == 2


# ── reports reproduce from the ledger ───────────────────────────────────

from faramir.reports import editorial_report, emerging  # noqa: E402


def _seed(tmp_path):
    led = Ledger(str(tmp_path / "ledger"))
    led.append("deliveries", [{"id": f"e{i}", "strategy": s, "suggestion_id": f"s{i}", "period": "2026-10-10"}
                              for i, s in enumerate(["overtake", "overtake", "chase", "countdown"])])
    led.append("editorial", [
        {"id": "a", "suggestion_id": "s0", "status": "accepted", "reason_codes": [], "observed": "2026-10-11"},
        {"id": "b", "suggestion_id": "s0", "status": "published", "reason_codes": [], "observed": "2026-10-12"},
        {"id": "c", "suggestion_id": "s1", "status": "rejected", "reason_codes": ["weak_comparison"], "observed": "2026-10-11"},
        {"id": "d", "suggestion_id": "s2", "status": "assigned", "reason_codes": [], "observed": "2026-10-11"}])
    return led


def test_editorial_report_reproduces_exactly(tmp_path):
    led = _seed(tmp_path)
    a = editorial_report.build(led)
    assert a == editorial_report.build(Ledger(str(tmp_path / "ledger")))
    ov = a["strategies"]["overtake"]
    assert (ov["delivered"], ov["published"], ov["rejected"], ov["acceptance_rate"]) == (2, 1, 1, 0.5)
    assert ov["rejection_reasons"] == {"weak_comparison": 1} and a["strategies"]["countdown"]["awaiting"] == 1


def test_emerging_strategies_report_never_promotes_automatically(tmp_path):
    led = Ledger(str(tmp_path / "ledger"))
    items = [{"id": f"d{i}", "film_key": f"F{i % 3}", "pattern_tags": ["limited_release"], "metric": "theater_avg"}
             for i in range(6)]
    led.append("discovery", items)
    led.append("discovery_editorial", [
        {"id": f"st{i}", "discovery_id": f"d{i}", "status": "published" if i < 3 else "accepted",
         "confirmed_tags": ["limited_release"], "published_url": f"https://movieweb.com/{i}/", "observed": "2026-10-11"}
        for i in range(6)])
    r = emerging.build(led)
    p = r["patterns"]["limited_release"]
    assert p["criteria"]["accepted_items"] == (6, True) and p["criteria"]["films"] == (3, True)
    assert p["criteria"]["sessions_14d_vs_cohort_median"] == "pending_outcomes" and not p["candidate"]
    assert set(p["unmet"]) == {"sessions_14d_vs_cohort_median", "rule_drafted"}
    outcomes = {f"https://movieweb.com/{i}/": 9000 for i in range(3)}
    r = emerging.build(led, drafted_rules={"limited_release"}, outcomes=outcomes, cohort_median_14d=5000)
    assert r["patterns"]["limited_release"]["candidate"] and "never automatic" in r["promotion"]


# ── pipeline ────────────────────────────────────────────────────────────

from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, pick_first_candidate  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_daily_run_reports_discovery_disabled(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    assert report["discovery"]["status"] == "history_collection_disabled"
