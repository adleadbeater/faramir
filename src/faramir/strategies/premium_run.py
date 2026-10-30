"""Premium Run (strategy 9): IMAX / premium-format screens and extensions.

**No verified source exists** (plan §3, §7): the strategy is not built and stays disabled. The
detector refuses to run without a verified premium-format source, so nothing can be inferred
from reported or press figures. Discovery may surface such reports only as ``needs_verification``.
"""

from __future__ import annotations

from faramir.strategies.common import DetectorResult

REQUIRED_SOURCE = "verified premium-format screen counts and extension dates (none available)"


def detect(premium_source: dict | None = None) -> DetectorResult:
    out = DetectorResult()
    out.rejected.append({"strategy": "premium_run", "film_key": None, "film": None, "axis": "premium", "target": "-",
                         "reason_code": "blocked_no_source", "detail": f"requires {REQUIRED_SOURCE}"})
    return out
