"""Cerebro (MediaWave) signal lookups for Faramir."""

import logging
from datetime import datetime, timedelta

from faramir.config import SIGNAL_LOOKBACK_DAYS, SIGNAL_MIN_SESSIONS, SIGNAL_MIN_ARTICLES

logger = logging.getLogger(__name__)


def load_cerebro(sheet_client, cerebro_sheet_id: str, lookback_days: int = SIGNAL_LOOKBACK_DAYS) -> list[dict]:
    """Read MovieWeb Article Analysis tab from Cerebro sheet. Return list of article dicts."""
    try:
        sh = sheet_client.open_by_key(cerebro_sheet_id)
        ws = sh.worksheet("MovieWeb Article Analysis")
        records = ws.get_all_records()
    except Exception as exc:
        logger.error("load_cerebro failed: %s", exc)
        return []

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    result = []

    for row in records:
        # Parse pub_datetime — try multiple column names
        dt_raw = row.get("pub_datetime") or row.get("pub_date") or ""
        dt = None
        if dt_raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(dt_raw)[:19], fmt)
                    break
                except ValueError:
                    continue
        if dt and dt < cutoff:
            continue
        result.append(row)

    logger.info("load_cerebro: %d rows after %d-day lookback", len(result), lookback_days)
    return result


def build_talent_signals(cerebro_rows: list[dict], names: list[str]) -> dict:
    """For each name, sum sessions and count articles where name appears in pri_tag or tags.

    Tags column is pipe-delimited.
    Returns {name: {sessions_180d, articles, is_signal}}.
    """
    result = {}
    for name in names:
        name_lower = name.lower()
        sessions_total = 0
        article_count = 0
        for row in cerebro_rows:
            pri_tag = str(row.get("pri_tag") or "").lower()
            tags = str(row.get("tags") or "").lower()
            tag_list = [t.strip() for t in tags.split("|") if t.strip()]
            if name_lower in pri_tag or name_lower in tag_list:
                sessions_total += int(row.get("ActSess") or row.get("act_sess") or 0)
                article_count += 1
        result[name] = {
            "sessions_180d": sessions_total,
            "articles": article_count,
            "is_signal": sessions_total >= SIGNAL_MIN_SESSIONS and article_count >= SIGNAL_MIN_ARTICLES,
        }
    return result


def build_franchise_signals(cerebro_rows: list[dict], collection_names: list[str]) -> dict:
    """For each collection name, aggregate sessions and article count.

    Returns {collection_name: {sessions_180d, articles, is_signal}}.
    """
    result = {}
    for name in collection_names:
        if not name:
            continue
        name_lower = name.lower()
        sessions_total = 0
        article_count = 0
        for row in cerebro_rows:
            pri_tag = str(row.get("pri_tag") or "").lower()
            tags = str(row.get("tags") or "").lower()
            tag_list = [t.strip() for t in tags.split("|") if t.strip()]
            if name_lower in pri_tag or name_lower in tag_list:
                sessions_total += int(row.get("ActSess") or row.get("act_sess") or 0)
                article_count += 1
        result[name] = {
            "sessions_180d": sessions_total,
            "articles": article_count,
            "is_signal": sessions_total >= SIGNAL_MIN_SESSIONS and article_count >= SIGNAL_MIN_ARTICLES,
        }
    return result
