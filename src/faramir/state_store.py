"""State read/write protocol: integrity checks, last-known-good backup,
recovery, and a run lock — all writes through the central guard.

Google Sheets has no multi-tab transactions, so every step is ordered so that
an interruption at any point leaves a recognisable, recoverable situation
(see docs/v2/increment4_review/README.md for the decision table):

  open()          read state_meta, state, state_lkg -> state_integrity.decide()
  recover()       ROLL_FORWARD: confirm a completed write
                  RESTORE: copy the verified backup back, keeping the live
                  tab's editor values; refuses on any editor-value conflict
  acquire_lock()  advisory lock row in state_meta (+ confirm re-read)
  write(rows)     1. re-check generation and lock (concurrency)
                  2. refresh state_lkg from the state validated at open()
                     (only ever from validated state), verify, confirm
                  3. mark state "writing" with the expected checksum
                  4. overwrite state in place + trim, read back, verify
                  5. confirm generation g+1
  release_lock()
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from faramir import runtime
from faramir.sheet import STATE_HEADERS, TabMissing, read_values, write_values
from faramir.state_integrity import (
    LKG_TAB,
    META_TAB,
    PROCEED,
    RESTORE,
    ROLL_FORWARD,
    STATE_TAB,
    Decision,
    checksum,
    decide,
    lock_holder,
    merge_editor_values,
    meta_values,
    parse_meta,
    records,
    row_values,
    split,
)

logger = logging.getLogger(__name__)

LOCK_TTL = timedelta(minutes=45)  # > the daily workflow's 15-minute timeout


class StateConflict(RuntimeError):
    """Another run holds or changed the state; this run must not write."""


class StateWriteError(RuntimeError):
    """A write could not be verified; meta is left showing the interruption."""


class StateRecoveryRefused(RuntimeError):
    """Automatic recovery is unsafe (e.g. editor values would be lost)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StateStore:
    def __init__(self, client, sheet_id: str, run_id: str, now_fn=_now):
        self.client = client
        self.sheet_id = sheet_id
        self.run_id = run_id
        self.now = now_fn
        self.decision: Decision | None = None
        self.generation: int | None = None
        self.lock_held = False
        self.header = list(STATE_HEADERS)

    # ── reads ────────────────────────────────────────────────────────────
    def _read_meta(self) -> dict | None:
        try:
            return parse_meta(read_values(self.client, self.sheet_id, META_TAB))
        except TabMissing:
            return None

    def _read_optional(self, tab: str) -> list[list[str]] | None:
        try:
            return read_values(self.client, self.sheet_id, tab)
        except TabMissing:
            return None

    def open(self) -> Decision:
        """Read and classify. Read failures raise SheetReadError (never 'empty')."""
        meta = self._read_meta()
        state_values = read_values(self.client, self.sheet_id, STATE_TAB) if meta is not None else []
        lkg_values = self._read_optional(LKG_TAB) if meta is not None else None
        d = decide(state_values, lkg_values, meta, self.header, self.run_id, self.now())
        self.decision = d
        if meta is not None and meta.get("state"):
            self.generation = int(meta["state"].get("generation") or 0)
        logger.info("State integrity: %s — %s", d.action, d.reason)
        return d

    @property
    def records(self) -> list[dict]:
        d = self.decision
        if d is None or d.action != PROCEED:
            raise RuntimeError("state records are only available after a PROCEED decision")
        return records(d.state.header, d.state.rows)

    # ── helpers ──────────────────────────────────────────────────────────
    def _dry(self) -> bool:
        ctx = runtime.current()
        return bool(ctx and ctx.dry_run)

    def _write_meta(self, meta: dict) -> None:
        write_values(self.client, self.sheet_id, META_TAB, meta_values(meta), "write_state_meta")

    def _verify(self, tab: str, expected_checksum: str, expected_rows: int) -> None:
        header, rows = split(read_values(self.client, self.sheet_id, tab))
        if header != self.header or len(rows) != expected_rows or checksum(header, rows) != expected_checksum:
            raise StateWriteError(f"{tab}: read-back verification failed ({len(rows)} rows)")

    def _stamp(self, entry: dict, **changes) -> dict:
        return {**entry, **{k: str(v) for k, v in changes.items()},
                "written_at": self.now().isoformat(timespec="seconds"), "run_id": self.run_id}

    # ── recovery ─────────────────────────────────────────────────────────
    def recover(self) -> str:
        d = self.decision
        # Fresh meta (keeps this run's lock row); refuse if the state entry moved since open().
        meta = self._read_meta() or {}
        if (meta.get("state") or {}).get("generation") != d.meta["state"].get("generation") or \
                (meta.get("state") or {}).get("status") != d.meta["state"].get("status"):
            raise StateConflict("state meta changed since this run opened it")
        if d.action == ROLL_FORWARD:
            meta["state"] = self._stamp(meta["state"], status="ok")
            self._write_meta(meta)
            return f"confirmed completed write of generation {meta['state']['generation']}"
        if d.action != RESTORE:
            raise RuntimeError(f"recover() called for {d.action}")

        live = read_values(self.client, self.sheet_id, STATE_TAB)
        merged, conflicts, preserved = merge_editor_values(self.header, d.lkg.rows, live, strict=True)
        if conflicts:
            raise StateRecoveryRefused("automatic restore would change editor values: " + "; ".join(conflicts[:5]))
        target_gen = meta["lkg"]["generation"]
        digest = d.lkg.checksum  # editor columns are outside the checksum
        meta["state"] = self._stamp(meta["state"], status="restoring", generation=target_gen,
                                    checksum=digest, row_count=len(merged))
        self._write_meta(meta)
        write_values(self.client, self.sheet_id, STATE_TAB, [self.header] + merged, "restore_state")
        if not self._dry():
            self._verify(STATE_TAB, digest, len(merged))
        meta["state"] = self._stamp(meta["state"], status="restored")
        self._write_meta(meta)
        self.generation = int(target_gen)
        return (f"restored state from verified backup generation {target_gen} "
                f"({len(merged)} rows; {preserved} editor value(s) kept from the live tab)")

    # ── lock ─────────────────────────────────────────────────────────────
    def acquire_lock(self) -> None:
        meta = self._read_meta()
        holder = lock_holder(meta, self.now())
        if holder and holder != self.run_id:
            raise StateConflict(f"state lock held by {holder}")
        meta["lock"] = self._stamp(meta.get("lock", {"key": "lock"}), status="held",
                                   expires_at=(self.now() + LOCK_TTL).isoformat(timespec="seconds"))
        self._write_meta(meta)
        if self._dry():
            return
        confirmed = self._read_meta() or {}
        if (confirmed.get("lock") or {}).get("run_id") != self.run_id:
            raise StateConflict(f"lost the lock race to {(confirmed.get('lock') or {}).get('run_id')}")
        self.lock_held = True

    def release_lock(self) -> None:
        if not self.lock_held:
            return
        meta = self._read_meta() or {}
        if (meta.get("lock") or {}).get("run_id") == self.run_id:
            meta["lock"] = self._stamp(meta["lock"], status="released", expires_at="")
            self._write_meta(meta)
        self.lock_held = False

    # ── write ────────────────────────────────────────────────────────────
    def write(self, rows: list[dict]) -> int:
        """Write a new generation of state. Returns the new generation number."""
        d = self.decision
        if d is None or d.action != PROCEED:
            raise RuntimeError("write() requires a PROCEED decision from open()")
        dry = self._dry()
        g = self.generation

        # 1. Concurrency: nobody else wrote or took the lock since open().
        meta = self._read_meta()
        if not dry:
            if str(meta["state"].get("generation")) != str(g) or meta["state"].get("status") not in ("ok", "restored"):
                raise StateConflict(f"state changed since this run opened it (now generation "
                                    f"{meta['state'].get('generation')}, {meta['state'].get('status')})")
            if not self.lock_held or (meta.get("lock") or {}).get("run_id") != self.run_id:
                raise StateConflict("this run does not hold the state lock")

        # 2. Editor values: always the live tab's latest.
        live = read_values(self.client, self.sheet_id, STATE_TAB)
        new_rows, conflicts, _ = merge_editor_values(self.header, row_values(self.header, rows), live, strict=False)
        if conflicts:
            raise StateConflict("live state tab has conflicting editor values: " + "; ".join(conflicts[:5]))

        # 3. Last-known-good backup — only ever from the state validated at open().
        lkg_meta = meta.get("lkg") or {"key": "lkg"}
        lkg_current = (d.lkg is not None and d.lkg.valid and str(lkg_meta.get("generation")) == str(g)
                       and lkg_meta.get("checksum") == d.state.checksum)
        if not lkg_current:
            backup_rows, _, _ = merge_editor_values(self.header, d.state.rows, live, strict=False)
            meta["lkg"] = self._stamp(lkg_meta, status="writing", generation=g)
            self._write_meta(meta)
            write_values(self.client, self.sheet_id, LKG_TAB, [self.header] + backup_rows, "write_state_lkg")
            if not dry:
                self._verify(LKG_TAB, d.state.checksum, len(backup_rows))
            meta["lkg"] = self._stamp(meta["lkg"], status="ok", generation=g, checksum=d.state.checksum,
                                      row_count=len(backup_rows))
            self._write_meta(meta)

        # 4. The new generation.
        expected = checksum(self.header, new_rows)
        meta["state"] = self._stamp(meta["state"], status="writing", generation=g + 1,
                                    checksum=expected, row_count=len(new_rows))
        self._write_meta(meta)
        write_values(self.client, self.sheet_id, STATE_TAB, [self.header] + new_rows, "write_state")
        if not dry:
            self._verify(STATE_TAB, expected, len(new_rows))

        # 5. Confirm.
        meta["state"] = self._stamp(meta["state"], status="ok")
        self._write_meta(meta)
        self.generation = g + 1
        self._snapshot(new_rows, expected, dry)
        return self.generation

    def _snapshot(self, rows: list[list[str]], digest: str, dry: bool) -> None:
        """Local off-Sheets copy of the written state (uploaded as a run artifact)."""
        ctx = runtime.current()
        if ctx is None:
            return
        os.makedirs(ctx.out_dir, exist_ok=True)
        name = "state_snapshot_proposed.json" if dry else "state_snapshot.json"
        with open(os.path.join(ctx.out_dir, name), "w", encoding="utf-8") as f:
            json.dump({"generation": self.generation, "checksum": digest, "header": self.header, "rows": rows,
                       "run_id": self.run_id}, f, indent=1)
