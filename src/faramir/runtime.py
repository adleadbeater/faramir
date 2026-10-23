"""Per-run context and the single write guard for every external write.

Every write Faramir makes — Google Sheets, Slack, or a repository file —
asks ``authorize(category, op, detail)`` first:

* no active run context        -> ``WriteBlocked`` (fail closed)
* run halted (e.g. corpus ABORT, failed preflight)
                               -> ``WriteBlocked`` for everything except the
                                  ``alert`` category (one failure alert)
* dry-run                      -> the operation is recorded locally and the
                                  caller must skip the real call (returns False)
* live and enabled             -> recorded, returns True (perform the write)

Categories: ``sheets`` (operational Sheets data), ``slack`` (picks/header/
init), ``alert`` (failure alerts only), ``repo`` (committed data files).
Local diagnostics under ``out/`` are not external writes and are not guarded.

Production is fail-closed: a live run requires ``FARAMIR_ENABLED`` to be
exactly ``true`` (case-insensitive). Dry-runs are allowed while disabled.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

CATEGORIES = {"sheets", "slack", "alert", "repo"}
LIVE, DRY_RUN = "live", "dry_run"


class WriteBlocked(RuntimeError):
    """An external write was attempted where none is permitted."""


def env_flag(name: str) -> bool:
    """True only for an explicit 'true' (case-insensitive). Unset/other = False."""
    return os.environ.get(name, "").strip().lower() == "true"


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


@dataclass
class RunContext:
    mode: str
    enabled: bool
    run_id: str
    out_dir: str
    fixtures_dir: str | None = None
    enabled_var: str = "FARAMIR_ENABLED"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    halted_reason: str | None = None
    operations: list[dict] = field(default_factory=list)  # every authorized/recorded/blocked write
    slack_preview: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    on_exit: list = field(default_factory=list)  # cleanups run before artifacts are written
    _dry_ts: int = 0

    @property
    def dry_run(self) -> bool:
        return self.mode == DRY_RUN

    def may_run(self) -> tuple[bool, str]:
        if self.dry_run:
            return True, "dry-run"
        if not self.enabled:
            return False, f"{self.enabled_var} is not 'true' — production run disabled (fail-closed)"
        if self.fixtures_dir:
            return False, "fixtures mode is offline-only and requires --dry-run"
        return True, "live"

    def halt(self, reason: str) -> None:
        """Stop all further operational writes for this run (alerts still allowed)."""
        if self.halted_reason is None:
            self.halted_reason = reason
        self.report.setdefault("halts", []).append(reason)

    def next_dry_ts(self) -> str:
        self._dry_ts += 1
        return f"dryrun-{self._dry_ts}"

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for op in self.operations:
            out[op["outcome"]] = out.get(op["outcome"], 0) + 1
        return out

    def write_artifacts(self) -> str:
        """Write local diagnostics: report.json, would_write.json, slack_preview.json."""
        os.makedirs(self.out_dir, exist_ok=True)
        report = {
            "run_id": self.run_id,
            "mode": self.mode,
            "enabled": self.enabled,
            "fixtures_dir": self.fixtures_dir,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "halted_reason": self.halted_reason,
            "write_outcomes": self.counts(),
            **self.report,
        }
        with open(os.path.join(self.out_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        with open(os.path.join(self.out_dir, "would_write.json" if self.dry_run else "writes.json"), "w", encoding="utf-8") as f:
            json.dump(self.operations, f, indent=2, default=str)
        with open(os.path.join(self.out_dir, "slack_preview.json"), "w", encoding="utf-8") as f:
            json.dump(self.slack_preview, f, indent=2, ensure_ascii=False, default=str)
        return self.out_dir


_CURRENT: RunContext | None = None


def current() -> RunContext | None:
    return _CURRENT


@contextlib.contextmanager
def use(ctx: RunContext | None):
    global _CURRENT
    previous, _CURRENT = _CURRENT, ctx
    try:
        yield ctx
    finally:
        _CURRENT = previous


def new_context(
    dry_run: bool,
    fixtures_dir: str | None = None,
    out_root: str | None = None,
    enabled_var: str = "FARAMIR_ENABLED",
) -> RunContext:
    root = out_root or os.path.join(os.path.dirname(__file__), "..", "..", "out")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    gh = os.environ.get("GITHUB_RUN_ID")
    run_id = f"{stamp}-{gh}" if gh else f"{stamp}-local-{uuid.uuid4().hex[:6]}"
    return RunContext(
        mode=DRY_RUN if dry_run else LIVE,
        enabled=env_flag(enabled_var),
        run_id=run_id,
        out_dir=os.path.abspath(os.path.join(root, run_id)),
        fixtures_dir=fixtures_dir,
        enabled_var=enabled_var,
    )


def authorize(category: str, op: str, detail: Any = None) -> bool:
    """Gate an external write. True = perform it; False = dry-run, skip it."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown write category {category!r}")
    ctx = _CURRENT
    entry = {"category": category, "op": op, "detail": _jsonable(detail)}
    if ctx is None:
        raise WriteBlocked(f"{category}.{op} attempted with no active run context")
    if ctx.halted_reason and category != "alert":
        ctx.operations.append({**entry, "outcome": "blocked", "reason": ctx.halted_reason})
        raise WriteBlocked(f"{category}.{op} blocked: run halted ({ctx.halted_reason})")
    if not ctx.dry_run and not ctx.enabled:
        ctx.operations.append({**entry, "outcome": "blocked", "reason": "disabled"})
        raise WriteBlocked(f"{category}.{op} blocked: production run disabled")
    if ctx.dry_run:
        ctx.operations.append({**entry, "outcome": "recorded"})
        return False
    ctx.operations.append({**entry, "outcome": "performed"})
    return True


def record_slack(kind: str, blocks: list[dict], channel: str | None = None, sent: bool = False) -> None:
    """Keep a rendered copy of an authorized Slack message for the local preview."""
    ctx = _CURRENT
    if ctx is not None:
        ctx.slack_preview.append({"kind": kind, "channel": channel, "sent": sent, "blocks": blocks})


def note(key: str, value: Any) -> None:
    """Add a diagnostic value to the run report (no-op without a context)."""
    ctx = _CURRENT
    if ctx is not None:
        ctx.report[key] = value
