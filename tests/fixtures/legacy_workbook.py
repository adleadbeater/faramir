"""A realistic copy of the production workbook as Faramir v1 left it (synthetic
values, real layouts): 17-column ``state`` with editor overrides and notes,
17-column ``suggestions`` with Slack timestamps, reactions and thread feedback
(including a duplicated pick and a row without pick ids), the ``Usage`` tab and
the corpus build's ``corpus_meta`` tab. No integrity, history or marker tabs.
"""

from __future__ import annotations

from faramir.sheet import LEGACY_STATE_HEADERS, SUGGESTIONS_HEADERS, USAGE_HEADERS


def _state_row(tmdb_id, title, status="active", dom=(140_000_000, 150_000_000), ww=(300_000_000, 320_000_000),
               override="", notes="", days=30, release="2026-09-18"):
    return [str(tmdb_id), f"tt{tmdb_id:07d}", title, title, release, "2026-09-20", "2026-10-21", status,
            str(dom[0]), str(dom[1]), str(ww[0]), str(ww[1]), str(days), override, notes, "0", "0"]


STATE = [
    list(LEGACY_STATE_HEADERS),
    _state_row(1001, "Harbor Lights"),
    _state_row(1002, "The Long Night", override="1009", notes="wrong film matched — editor fix"),
    _state_row(1003, "Paper Kites", dom=(88_000_000, 91_500_000), ww=(0, 0), notes="no worldwide yet"),
    _state_row(1004, "Old Engine", status="retired", dom=(212_000_000, 212_000_000), days=95),
    _state_row(1005, "Glass Garden", dom=(25_000_000, 27_100_000), ww=(60_000_000, 64_000_000), days=10),
    _state_row(1006, "Signal Fire", dom=(310_000_000, 311_000_000), ww=(905_000_000, 909_000_000), days=60,
               override="", notes="watch $1B"),
]


def _pick(run_date, active, comp, kind="just_passed", threshold="", slack_ts="", up="", down="", replies="", fb=""):
    return [run_date, str(active), f"Film {active}", str(comp), f"Comp {comp}" if comp not in ("", "None") else "",
            "domestic", kind, threshold, "franchise_sibling", f"Film {active} Passes Comp {comp}", "An angle.",
            "4.2", slack_ts, up, down, replies, fb]


SUGGESTIONS = [
    list(SUGGESTIONS_HEADERS),
    _pick("2026-10-15", 1001, 2001, slack_ts="1760540401.000100", up="3", down="0", replies="2", fb="Good — assign to Dana"),
    _pick("2026-10-16", 1002, "None", kind="milestone", threshold="100000000", slack_ts="1760626801.000200", up="1"),
    _pick("2026-10-18", 1003, 2003, slack_ts="1760799601.000300"),
    _pick("2026-10-18", 1003, 2003, slack_ts="1760799602.000400"),                 # same pick posted twice
    _pick("2026-10-19", "", "", slack_ts="1760886001.000500", fb="headline-only row"),  # no pick ids
    _pick("2026-10-20", 1005, 2005),                                                # never posted (no ts)
    _pick("2026-10-21", 1006, 2006, slack_ts="1761058801.000000", down="2", fb="not for us"),
]

USAGE = [list(USAGE_HEADERS),
         ["2026-10-21T15:01:02+00:00", "select_picks", "41000", "1800", "0", "0", "claude-sonnet-4-6"]]

CORPUS_META = [["gross_as_of", "rows", "built_at"], ["2026-10-01", "2011", "2026-10-01T09:00:00+00:00"]]


def build(book):
    """Load the legacy tabs into a FakeSpreadsheet."""
    book.add_tab("state", [list(r) for r in STATE])
    book.add_tab("suggestions", [list(r) for r in SUGGESTIONS])
    book.add_tab("Usage", [list(r) for r in USAGE])
    book.add_tab("corpus_meta", [list(r) for r in CORPUS_META])
    return book
