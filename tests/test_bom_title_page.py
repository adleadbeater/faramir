"""Box Office Mojo title page: lifetime domestic / international / worldwide from the "All Releases" summary."""

import os

from faramir import bom_fetch

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "bom", "title_tt35538033.html")


def test_real_page_reads_lifetime_totals_not_the_opening_weekend():
    # Resident Evil (2026), saved 2026-10-23: opening $60,152,878; the old parser returned that as domestic, intl 0
    got = bom_fetch.parse_title_page(open(FIXTURE, encoding="utf-8").read())
    assert got == {"domestic": 150_249_362, "international": 213_013_664, "worldwide": 363_263_026}
    assert got["domestic"] + got["international"] == got["worldwide"]


def test_domestic_only_film_has_zero_international():
    html = ('<div class="mojo-performance-summary-table">'
            '<div><span class="a-size-small">Domestic (<span class="percent">100%</span>)</span>'
            '<span class="money">$5,000,000</span></div>'
            '<div><span class="a-size-small">International</span><span>–</span></div>'
            '<div><span class="a-size-small">Worldwide</span><span class="money">$5,000,000</span></div></div>')
    assert bom_fetch.parse_title_page(html) == {"domestic": 5_000_000, "international": 0, "worldwide": 5_000_000}


def test_page_without_summary_is_unreadable():
    assert bom_fetch.parse_title_page("<html><div>Domestic</div><div>$60,152,878</div></html>") is None
