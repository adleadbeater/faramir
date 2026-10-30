"""Synthetic sanitized Cerebro snapshots (invented titles and figures; the real column layout)."""


from __future__ import annotations

import csv
import hashlib
from datetime import date, timedelta

HEADER = ["title", "url", "pub_date", "pub_type", "brand", "segment", "net_cat", "pri_cat", "pri_tag", "tags",
          "content_type", "page_type", "intent", "sessions", "target_sessions", "pageviews", "discover_clicks",
          "discover_impressions", "search_clicks"]
AS_OF = date(2026, 10, 20)


def row(title, sessions, days_old=30, content_type="News", page_type="Regular", pub_type="Publish", brand="MW",
        pri_tag="Box Office", tags="Box Office", impressions=2000, url=None):
    d = AS_OF - timedelta(days=days_old)
    return {"title": title, "url": url or f"https://movieweb.com/{int(hashlib.sha1(title.encode()).hexdigest(), 16) % 10**8}/", "pub_date": f"{d} 10:00:00",
            "pub_type": pub_type, "brand": brand, "segment": "Movies", "net_cat": "movies", "pri_cat": "Movie News",
            "pri_tag": pri_tag, "tags": tags, "content_type": content_type, "page_type": page_type, "intent": "Short-Term",
            "sessions": str(sessions), "target_sessions": "5000", "pageviews": str(sessions), "discover_clicks": "0",
            "discover_impressions": str(impressions), "search_clicks": "0"}


def write(path, rows, header=HEADER):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def box_office_rows():
    """40 matured MW News box-office articles across constructions, plus rows the contract must exclude."""
    rows = []
    for i in range(12):
        rows.append(row(f"Film {i} Passes Classic {i} at the Box Office", 9000 + 100 * i))
    for i in range(10):
        rows.append(row(f"Film {i} Crosses $500 Million Worldwide", 6000 + 50 * i))
    for i in range(10):
        rows.append(row(f"Film {i} Nears Classic {i}'s Domestic Total", 3000 + 20 * i))
    for i in range(5):
        rows.append(row(f"Film {i} Opens to $40 Million", 4000))
    for i in range(3):
        rows.append(row(f"$1 Billion: Film {i} Officially Joins the Club", 12000))
    rows += [
        row("Film X Passes Classic X (too new)", 99999, days_old=3),                       # immature
        row("Film Y Passes Classic Y (republish)", 99999, pub_type="Republish"),
        row("Film Z Passes Classic Z (video)", 99999, content_type="Video", page_type="Video"),
        row("Film W Passes Classic W (feature)", 99999, content_type="Feature"),            # Features/Lists team
        row("Film V Passes Classic V (other brand)", 99999, brand="SR"),
        row("Unrelated News Story", 50000, pri_tag="Marvel", tags="Marvel"),
    ]
    return rows
