"""Increment 3: simulated failure scenarios on the full daily pipeline.

Each scenario proves which external writes did or did not happen, using the
raw fakes underneath the guard (FakeClient.calls, FakeHTTP.calls).
"""

import json
import os
import subprocess
import sys

import pytest

from tests.fakes.fake_anthropic import FakeAnthropic
from tests.test_run_daily_corpus import ROOT, V2_PATH, env, load_run_daily, pick_first_candidate  # noqa: F401

FIXTURE_DAY = os.path.join(ROOT, "fixtures", "offline", "2026-10-22")


def main(env, *args):
    return load_run_daily().main(["--out-root", str(env["tmp"] / "out"), *args])


def artifacts(env):
    (run_dir,) = (env["tmp"] / "out").iterdir()
    def load(name):
        p = run_dir / name
        return json.loads(p.read_text()) if p.exists() else None
    return load("report.json"), load("would_write.json") or load("writes.json"), load("slack_preview.json")


def posts(http):
    return [c for c in http.calls if c["method"] == "POST"]


# ── kill switch ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "false", "0", "yes"])
def test_disabled_live_run_does_nothing(env, value):
    if value is None:
        env["monkeypatch"].delenv("FARAMIR_ENABLED", raising=False)
    else:
        env["monkeypatch"].setenv("FARAMIR_ENABLED", value)
    fake = FakeAnthropic([]).install(env["monkeypatch"])
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)

    assert main(env) == 0
    assert env["gc"].calls == []          # not even a Sheets read
    assert env["http"].calls == []        # no network of any kind
    assert fake.calls == []
    report, writes, preview = artifacts(env)
    assert "FARAMIR_ENABLED is not 'true'" in report["not_started"]
    assert writes == [] and preview == []


def test_enabled_requires_explicit_true_and_runs(env):
    env["monkeypatch"].setenv("FARAMIR_ENABLED", "TRUE")
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])
    assert main(env) == 0
    assert len(env["slack"].messages) == 1


# ── corpus ABORT: no operational writes, one alert ──────────────────────

def test_corpus_abort_live_writes_nothing_but_the_alert(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", os.path.join(ROOT, "data", "corpus.json"))
    fake = FakeAnthropic([]).install(env["monkeypatch"])

    assert main(env) == 3
    assert env["gc"].calls == []                      # preflight halts before Sheets is touched
    assert fake.calls == []
    (alert,) = posts(env["http"])
    assert alert["url"].startswith("https://hooks.slack.com/")
    report, writes, _ = artifacts(env)
    assert report["halted_reason"].startswith("corpus ABORT") and report["preflight"]["ok"] is False
    assert [(w["category"], w["outcome"]) for w in writes] == [("alert", "performed")]


def test_corpus_abort_dry_run_sends_nothing(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", os.path.join(ROOT, "data", "corpus.json"))
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env, "--dry-run") == 3
    assert env["http"].calls == [] and env["gc"].calls == []
    report, writes, preview = artifacts(env)
    assert [(w["category"], w["outcome"]) for w in writes] == [("alert", "recorded")]
    assert preview[0]["kind"] == "webhook:alert" and preview[0]["sent"] is False


def test_failed_preflight_missing_settings_writes_nothing(env):
    env["monkeypatch"].delenv("FARAMIR_SHEET_ID")
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([]).install(env["monkeypatch"])
    assert main(env) == 3
    assert env["gc"].calls == [] and env["http"].calls == []
    report, writes, _ = artifacts(env)
    assert report["preflight"] == {"ok": False, "missing_settings": ["FARAMIR_SHEET_ID"]}
    assert writes == []


def test_unhandled_error_sends_only_an_alert(env):
    from faramir import sheet

    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    FakeAnthropic([]).install(env["monkeypatch"])

    def boom():
        raise RuntimeError("sheets auth failed")

    env["monkeypatch"].setattr(sheet, "get_sheet_client", boom)
    assert main(env) == 1
    (alert,) = posts(env["http"])
    assert "Faramir failed" in json.dumps(alert["json"])
    report, writes, _ = artifacts(env)
    assert "sheets auth failed" in report["error"]
    assert [(w["category"], w["outcome"]) for w in writes] == [("alert", "performed")]


# ── dry-run on the full pipeline: zero external writes ──────────────────

def test_dry_run_full_pipeline_makes_zero_external_writes(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    env["monkeypatch"].delenv("FARAMIR_ENABLED")  # dry-run works while disabled
    FakeAnthropic([pick_first_candidate]).install(env["monkeypatch"])

    assert main(env, "--dry-run") == 0
    assert env["gc"].writes() == []                   # reads happened, writes did not
    assert posts(env["http"]) == []                   # no Slack post of any kind
    report, writes, preview = artifacts(env)
    ops = [(w["category"], w["op"], w["outcome"]) for w in writes]
    assert ("sheets", "write_state", "recorded") in ops
    assert ("sheets", "append_suggestions", "recorded") in ops
    assert ("slack", "chat.postMessage", "recorded") in ops
    assert all(o == "recorded" for _, _, o in ops)
    assert [p["kind"] for p in preview] == ["webhook:slack", "chat.postMessage"]
    assert report["picks"] == {"returned": 1, "after_validation": 1}


def test_run_date_override_only_in_dry_run(env):
    env["monkeypatch"].setenv("FARAMIR_CORPUS_PATH", V2_PATH)
    assert main(env, "--run-date", "2026-10-22") == 2
    assert env["gc"].calls == [] and env["http"].calls == []


# ── fixture-only offline execution, no credentials, no network ──────────

def _clean_env():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}


def test_offline_fixture_run_needs_no_credentials_or_network(tmp_path):
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "run_daily.py"),
         "--dry-run", "--fixtures", FIXTURE_DAY, "--out-root", str(tmp_path)],
        env=_clean_env(), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    (run_dir,) = tmp_path.iterdir()
    report = json.loads((run_dir / "report.json").read_text())
    writes = json.loads((run_dir / "would_write.json").read_text())
    preview = json.loads((run_dir / "slack_preview.json").read_text())
    assert report["mode"] == "dry_run" and report["enabled"] is False
    assert report["corpus"]["status"] == "OK" and report["picks"]["after_validation"] == 1
    assert writes and all(w["outcome"] == "recorded" for w in writes)
    assert {w["op"] for w in writes} >= {"write_state", "append_suggestions", "chat.postMessage", "webhook_post"}
    assert any("[OFFLINE FIXTURE]" in json.dumps(p["blocks"]) for p in preview)


def test_offline_mode_refuses_real_network_and_unrouted_requests():
    import socket

    import requests

    from faramir import offline

    with offline.activate(FIXTURE_DAY):
        with pytest.raises(offline.OfflineNetworkRefused):
            socket.create_connection(("example.com", 80))
        with pytest.raises(offline.OfflineRequestRefused):
            requests.get("https://example.com/")
        ws = offline.sheet_client().open_by_key("offline-sheet").worksheet("state")
        assert ws.get_all_records()[0]["canonical_title"] == "Beta Story"
        with pytest.raises(offline.OfflineWriteRefused):
            ws.update(values=[["x"]], range_name="A1")


def test_fixtures_without_dry_run_are_refused(env):
    assert main(env, "--fixtures", FIXTURE_DAY) == 0
    report, _, _ = artifacts(env)
    assert "requires --dry-run" in report["not_started"]
    assert env["gc"].calls == [] and env["http"].calls == []
