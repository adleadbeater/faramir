"""Synthetic The Numbers chart pages for parser tests.

Reproduces the layout verified on the 2026-10-16 weekend and 2026-10-21 daily
captures (h1 heading, ``chart-desktop`` table with ``<br>``-split headers and a
non-breaking space in "Total Gross", ``chart-mobile`` twin, ranks then ``-``,
``(new)``/``(n)``/``(-)`` prev cells, one-cell spacer row, "Reporting movies"
footer). Titles and figures are invented; none of this is TN content.
"""

from __future__ import annotations

from datetime import date

WEEKEND_HEADERS = ["Rank", "Prev", "Title", "Gross", "Weekly<br/>Change", "Theaters", "Theater<br/>Average",
                   "Total&nbsp;Gross", "Days in Release"]
DAILY_HEADERS = ["Rank", "Prev", "Title", "Gross", "Daily<br/>Change", "Weekly<br/>Change", "Theaters",
                 "Theater<br/>Average", "Total&nbsp;Gross", "Days in Release"]

# rank, prev, title, slug, gross, weekly change, theaters, total gross, days
WEEKEND_FILMS = [
    ("1", "(new)", "Iron Harbor", "Iron-Harbor-(2026)", 31_250_000, "", 3_600, 31_250_000, 3),
    ("2", "(1)", "Quiet Orchard", "Quiet-Orchard-(2026)", 12_400_000, "-52%", 3_310, 58_900_000, 10),
    ("3", "(new)", "Night Ferry", "Night-Ferry-(2026-United-Kingdom)", 6_100_000, "", 1_200, 6_100_000, 3),
    ("4", "(2)", "Paper Lanterns", "Paper-Lanterns-(2026)", 4_800_000, "-38%", 2_950, 88_700_000, 24),
    ("5", "(3)", "Galaxy Run", "Galaxy-Run-(2019)", 1_150_000, "+1,224%", 410, 640_000_000, 2_733),  # re-release
    ("-", "(-)", "Le Voyage", "Voyage-Le-(2024-France)", 210_000, "+12%", 95, 1_350_000, 59),         # late US release
    ("-", "(9)", "Old Reel", "Old-Reel-(1985)", 95_000, "-20%", 60, 210_400_000, 15_086),             # re-release
    ("-", "(14)", "Small Indie", "Small-Indie-(2026)", 20_000, "-3%", 50, 1_100_000, 40),
]


def _money(v):
    return f"${v:,}"


def _avg(gross, theaters):
    return _money(round(gross / theaters)) if theaters else ""


def page(kind: str = "weekend", day: date = date(2026, 10, 16), films=None, headers=None, footer=True,
         footer_count=None, footer_total=None, heading=None, row_hook=None, desktop_class=True) -> str:
    films = list(WEEKEND_FILMS if films is None else films)
    if headers is None:
        headers = WEEKEND_HEADERS if kind == "weekend" else DAILY_HEADERS
    if heading is None:
        heading = (f"Weekend Domestic Box Office {day:%B} {day.day}, {day.year}" if kind == "weekend"
                   else f"Daily Domestic Box Office {day:%A}, {day:%B} {day.day}, {day.year}")
    names = [h.replace("<br/>", " ").replace("&nbsp;", " ") for h in headers]
    rows = []
    for f in films:
        rank, prev, title, slug, gross, change, theaters, total, days = f
        values = {
            "Rank": rank, "Prev": prev, "Title": f'<b><a href="/movie/{slug}">{title}</a></b>' if slug else title,
            "Gross": _money(gross) if isinstance(gross, int) else gross,
            "Daily Change": change, "Weekly Change": change,
            "Theaters": f"{theaters:,}" if isinstance(theaters, int) else theaters,
            "Theater Average": _avg(gross, theaters) if isinstance(gross, int) and isinstance(theaters, int) else "",
            "Total Gross": _money(total) if isinstance(total, int) else total,
            "Days in Release": f"{days:,}" if isinstance(days, int) else (days or ""),
        }
        cells = [values.get(n, "") for n in names]
        if row_hook:
            cells = row_hook(f, cells)
        rows.append("<tr>" + "".join(f"<td>{c or '&nbsp;'}</td>" for c in cells) + "</tr>")
    count = len(films) if footer_count is None else footer_count
    total = sum(f[4] for f in films if isinstance(f[4], int)) if footer_total is None else footer_total
    foot = (f'<tr><td>&nbsp;</td></tr><tr><td></td><td></td><td>Reporting movies: {count}</td>'
            f'<td>{_money(total)}</td><td>total box office</td></tr>') if footer else ""
    cls = ' class="chart-desktop"' if desktop_class else ""
    mobile = ('<table class="chart-mobile"><tr><th>Rank</th><th>Title</th><th>Daily</th><th>Theaters</th><th>Total</th></tr>'
              '<tr><td>1</td><td>nested mobile markup ignored</td></tr></table>')
    return (f"<html><head><title>The Numbers - synthetic</title></head><body><h1>{heading}</h1><h2>Quick Links</h2>"
            f"<table{cls}><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
            + "".join(rows) + foot + "</table>" + mobile + "</body></html>")
