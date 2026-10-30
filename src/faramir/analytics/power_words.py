"""Power Words: proven title phrases (sanitized snapshot of the Power Words sheet).

Format (as maintained for aragorn/arwen): ``{"source", "count", "phrases": [{"phrase", "mw": {use,
sessions, spa, fr, hh}, "sr": {...}, "note"}]}``. ``mw`` is MovieWeb, ``sr`` Screen Rant.

Only MovieWeb figures count for Faramir (brands are never pooled); a phrase is ``proven`` on
MovieWeb when it has at least ``MIN_USES`` [CAL] MovieWeb uses. ``spa`` is sessions per article
(cumulative), ``fr`` failure rate %, ``hh`` heavy-hitter rate %.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

MIN_USES = 3


@dataclass
class PowerWord:
    phrase: str
    uses: int
    spa: float | None
    failure_rate: float | None
    heavy_hitter_rate: float | None

    @property
    def proven(self) -> bool:
        return self.uses >= MIN_USES and self.spa is not None


def load(path: str) -> list[PowerWord]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for p in data.get("phrases", []):
        mw = p.get("mw") or {}
        out.append(PowerWord(str(p.get("phrase", "")).strip(), int(mw.get("use") or 0), mw.get("spa"),
                             mw.get("fr"), mw.get("hh")))
    return [w for w in out if w.phrase]


def configured() -> list[PowerWord]:
    path = os.environ.get("FARAMIR_POWER_WORDS")
    return load(path) if path and os.path.exists(path) else []


def found_in(headline: str, words: list[PowerWord]) -> list[PowerWord]:
    text = headline.lower()
    return [w for w in words if re.search(rf"(?<![\w]){re.escape(w.phrase.lower())}(?![\w])", text)]
