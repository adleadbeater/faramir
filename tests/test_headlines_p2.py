"""Phase 2: Cerebro snapshot contract, Power Words, construction taxonomy, historical analysis,
recommendations, evidence-backed rank claims and pipeline integration. Synthetic data only."""

import json
import os
from datetime import date

import pytest

from faramir.analytics import audience, cerebro_snapshot as CS, power_words as PW
from faramir.headlines import taxonomy
from faramir.headlines.analysis import analyze
from faramir.headlines.recommend import recommend
from tests.fixtures import cerebro_synth as SYN

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def snap(tmp_path):
    return CS.load(SYN.write(tmp_path / "c.csv", SYN.box_office_rows()), as_of=SYN.AS_OF)


# ── snapshot contract ───────────────────────────────────────────────────

def test_sensitive_columns_are_refused(tmp_path):
    rows = [{**SYN.row("A Passes B", 1000), "Writer": "someone", "Cost": "10"}]
    path = SYN.write(tmp_path / "raw.csv", rows, header=SYN.HEADER + ["Writer", "Cost"])
    with pytest.raises(CS.SensitiveColumns):
        CS.load(path)


def test_ui_export_headers_are_mapped_and_unknown_columns_ignored(tmp_path):
    hdr = ["Title", "URL", "Pub. Date", "Pub. Type", "Brand", "Content Type", "Page Type", "Actual Sessions", "Mystery"]
    path = tmp_path / "ui.csv"
    with open(path, "w") as f:
        f.write(",".join(hdr) + "\nA Passes B,u,2026-09-01 10:00:00,Publish,MW,News,Regular,1234,x\n")
    s = CS.load(str(path), as_of=SYN.AS_OF)
    assert s.articles[0].sessions == 1234 and s.ignored_columns == ["Mystery"]


def test_contract_filters_and_team_pools(snap):
    news = snap.pool("news")
    titles = {a.title for a in news}
    assert len(news) == 41 and "Unrelated News Story" in titles
    for excluded in ("too new", "republish", "video", "feature", "other brand"):
        assert not any(excluded in t for t in titles)
    with pytest.raises(ValueError):
        snap.pool("everything")


def test_features_pool_is_separate(tmp_path):
    s = CS.load(SYN.write(tmp_path / "c.csv", SYN.box_office_rows()), as_of=SYN.AS_OF)
    fl = s.pool("features_lists")
    assert [a.title for a in fl] == ["Film W Passes Classic W (feature)"]
    assert all(a.team() == "news" for a in s.pool("news"))


def test_metrics_are_labelled_cumulative_with_ages(snap):
    m = CS.metrics(snap.pool("news"), snap.as_of)
    assert m.basis.startswith("cumulative sessions per article") and "not calendar-period" in m.basis
    assert (m.age_days_min, m.age_days_max) == (30, 30) and m.failure_rate == 0.0


def test_as_of_is_explicit_or_inferred(tmp_path):
    s = CS.load(SYN.write(tmp_path / "c.csv", SYN.box_office_rows()))
    assert s.as_of_basis == "latest_pub_date" and s.as_of == date(2026, 10, 17)


# ── Power Words (MovieWeb figures only) ─────────────────────────────────

def _words(tmp_path):
    data = {"phrases": [{"phrase": "Officially", "mw": {"use": 12, "spa": 3200, "fr": 10, "hh": 5}, "sr": {"use": 400, "spa": 9000}},
                        {"phrase": "Box Office", "mw": {"use": 2, "spa": 5000}, "sr": {"use": 900, "spa": 12000}},
                        {"phrase": "Masterpiece", "mw": {"use": 30, "spa": 15000}, "sr": {}}]}
    p = tmp_path / "pw.json"
    p.write_text(json.dumps(data))
    return PW.load(str(p))


def test_power_words_use_movieweb_figures_only(tmp_path):
    words = _words(tmp_path)
    off = next(w for w in words if w.phrase == "Officially")
    assert (off.uses, off.spa, off.proven) == (12, 3200, True)
    assert not next(w for w in words if w.phrase == "Box Office").proven       # 2 MW uses (SR's 900 don't count)
    assert [w.phrase for w in PW.found_in("Wicked Officially Passes Barbie", words)] == ["Officially"]


# ── taxonomy ────────────────────────────────────────────────────────────

def test_tagger_agrees_with_hand_labels():
    labels = json.load(open(os.path.join(FIX, "headline_labels.json")))
    agree = sum(1 for h, want in labels if taxonomy.primary(h) == want)
    misses = [(h, want, taxonomy.primary(h)) for h, want in labels if taxonomy.primary(h) != want]
    assert agree / len(labels) >= 0.9, misses


def test_multi_label_tagging():
    assert taxonomy.tag("$1 Billion: Inside Out 2 Officially Passes Frozen II") == ["lead_with_number", "passes_comparison"]
    assert taxonomy.tag("Box Office Update") == ["other"]


def test_constructions_fit_their_strategy():
    assert "passes_comparison" not in taxonomy.FITS["chase"]           # a Chase hasn't passed yet
    assert "anticipation" not in taxonomy.FITS["overtake"]
    assert "chart_position" in taxonomy.FITS["takeover"]


# ── historical analysis ─────────────────────────────────────────────────

def test_analysis_on_the_box_office_pool(snap):
    a = analyze(snap)
    assert a.articles == 40 and a.label == "historical_not_faramir"   # the unrelated story is not box office
    passes = a.stat("passes_comparison")
    assert passes.n == 12 and not passes.experimental and passes.lift_vs_baseline > 0
    assert a.stat("lead_with_number").experimental                     # n=3 < 8
    assert all(r["label"] == "historical_not_faramir" for r in a.audit_sample)
    assert 0 < len(a.audit_sample) < a.articles


def test_audit_sample_is_deterministic(snap):
    assert [r["url"] for r in analyze(snap).audit_sample] == [r["url"] for r in analyze(snap).audit_sample]


# ── recommendations ─────────────────────────────────────────────────────

def test_recommendation_ranks_measured_fitting_constructions(snap, tmp_path):
    rec = recommend("overtake", analyze(snap), _words(tmp_path))
    order = [c["construction"] for c in rec.constructions]
    assert order[0] == "passes_comparison" and order.index("lead_with_number") > order.index("milestone_crossing")
    assert rec.constructions[-1]["rank_basis"] in ("no_history", "experimental")
    assert any(c.startswith("construction:passes_comparison:+") and "n=12" in c for c in rec.rationale_codes)
    assert [w["phrase"] for w in rec.power_words] == ["Officially"]      # proven, MW, event-safe
    assert rec.guidance()["preferred_constructions"][0] == "passes_comparison"


def test_recommendation_never_suggests_unfitting_or_unsafe_options(snap, tmp_path):
    rec = recommend("chase", analyze(snap), _words(tmp_path))
    assert {c["construction"] for c in rec.constructions} <= set(taxonomy.FITS["chase"])
    assert "Masterpiece" not in [w["phrase"] for w in rec.power_words]


def test_recommendation_without_snapshot_says_so():
    rec = recommend("overtake", None)
    assert rec.calibration == "no_evidence" and rec.rationale_codes[0].startswith("no_cerebro_snapshot")


# ── audience signals from the snapshot ──────────────────────────────────

def test_audience_signals(tmp_path):
    rows = SYN.box_office_rows() + [SYN.row(f"Star Name news {i}", 30000, pri_tag="Star Name") for i in range(4)]
    s = CS.load(SYN.write(tmp_path / "c.csv", rows), as_of=SYN.AS_OF)
    sig = audience.signals(s, ["Star Name", "Nobody"])
    assert sig["Star Name"]["is_signal"] and sig["Star Name"]["tier"] == "high" and sig["Star Name"]["articles"] == 4
    assert sig["Nobody"]["tier"] == "unknown" and audience.affinity_score(sig["Nobody"]) == 0.5
    assert "cumulative sessions per article" in sig["Star Name"]["basis"]


# ── evidence-backed rank language (SR-1) ────────────────────────────────

from faramir.validate import lexical_checks, check_claims  # noqa: E402


def _rank_pick(headline, rank=None):
    ev = {"event": {"axis": "domestic", "kind": "just_passed", "milestone_type": None, "threshold": None},
          "active": {"title": "New Hit", "genres": []}, "comp": None, "categories": []}
    if rank is not None:
        ev["strategy_event"] = {"strategy": "takeover", "chart_rank": rank}
    return {"active_title": "New Hit", "headline": headline, "angle": "", "claims": [], "evidence": ev}


@pytest.mark.parametrize("headline, rank, ok", [
    ("New Hit Takes No. 1 at the Weekend Box Office", 1, True),
    ("New Hit Tops the Weekend Box Office", 1, True),
    ("New Hit Is Number One", 1, True),
    ("New Hit Takes No. 1 at the Weekend Box Office", None, False),   # no chart evidence: still blocked
    ("New Hit Takes No. 2", 1, False),                                # contradicts the evidence
    ("New Hit Tops the Weekend Box Office", 2, False),
    ("New Hit Has the Biggest Weekend Ever", 1, False),               # superlatives stay blocked
])
def test_rank_claims_need_chart_evidence(headline, rank, ok):
    reasons = [r for r in lexical_checks(_rank_pick(headline, rank), []) if r.startswith(("lexical_superlative", "rank_mismatch"))]
    assert (reasons == []) is ok, reasons


def test_chart_position_claim_supported_only_with_evidence():
    p = _rank_pick("x", 1)
    p["claims"] = [{"type": "chart_position", "text": "No. 1"}]
    assert not any(r.startswith("claim_unsupported:chart_position") for r in check_claims(p))
    p = _rank_pick("x")
    p["claims"] = [{"type": "chart_position", "text": "No. 1"}]
    assert any(r.startswith("claim_unsupported:chart_position") for r in check_claims(p))


# ── pipeline ────────────────────────────────────────────────────────────

from tests.fakes.fake_anthropic import FakeAnthropic  # noqa: E402
from tests.test_run_daily_corpus import V2_PATH, env, pick_first_candidate  # noqa: E402,F401
from tests.test_run_daily_runtime import artifacts, main  # noqa: E402


def test_daily_run_uses_the_snapshot_and_passes_guidance(env, tmp_path):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setenv("FARAMIR_CEREBRO_SNAPSHOT", SYN.write(tmp_path / "c.csv", SYN.box_office_rows()).__str__())
    fake = FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    assert report["cerebro_source"]["source"] == "snapshot"
    assert report["headline_intelligence"]["box_office_articles"] == 40
    payload = json.loads(fake.calls[0]["messages"][0]["content"])
    g = payload["active_films"][0]["candidates"][0]["headline_guidance"]
    assert g["preferred_constructions"] and g["evidence"]
    from faramir.suggestions import read_table

    (row,) = read_table(env["gc"], "S").rows
    ev = json.loads(row.get("evidence_json"))
    assert ev["headline"]["constructions"] and ev["headline"]["calibration"] == "historical_prior"


def test_daily_run_refuses_a_sensitive_snapshot_and_continues(env, tmp_path):
    rows = [{**SYN.row("A Passes B", 1000), "Writer": "x"}]
    bad = SYN.write(tmp_path / "raw.csv", rows, header=SYN.HEADER + ["Writer"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].setenv("FARAMIR_CEREBRO_SNAPSHOT", str(bad))
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    report, _, _ = artifacts(env)
    assert report["cerebro_source"]["source"] == "none" and report["headline_intelligence"]["snapshot"] is False


def test_analysis_script_writes_only_to_out(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ha", os.path.join(os.path.dirname(__file__), "..", "scripts", "headline_analysis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "out"
    assert mod.main(["--snapshot", str(SYN.write(tmp_path / "c.csv", SYN.box_office_rows())), "--as-of", "2026-10-20",
                     "--out", str(out)]) == 0
    assert sorted(os.listdir(out)) == ["audit_sample.csv", "summary.json", "summary.md"]
