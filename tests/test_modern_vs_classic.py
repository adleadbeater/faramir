"""Modern vs. Classic (Rob, 2026-11-03): films 25+ years old are compared in today's dollars, the card says how,
and a daily floor of two cards. Ticket prices: data/ticket_prices.json."""

import copy
from datetime import date

import pytest

from types import SimpleNamespace

from faramir import cards, config, inflation, links
from faramir.corpus import build_index, reported
from faramir.strategies import chase, overtake
from faramir.strategies.common import comp_strength

TODAY = date(2026, 11, 3)


def classic(**kw):
    base = {"tmdb_id": 642, "imdb_id": "tt0064115", "title": "Butch Cassidy and the Sundance Kid", "release_year": "1969",
            "genres": ["Western", "Crime", "Drama"], "director": "George Roy Hill", "lead_cast": ["Paul Newman"],
            "collection_id": None, "domestic_lifetime": 102_308_889, "worldwide_lifetime": 102_316_607,
            "domestic_status": "reported", "worldwide_status": "reported", "identity_status": "verified"}
    return {**base, **kw}


def modern(**kw):
    base = {"tmdb_id": 1, "imdb_id": "tt0000001", "title": "Other Mommy", "canonical_title": "Other Mommy",
            "genres": ["Horror", "Thriller"], "director": "Rob Savage", "lead_cast": ["Jessica Chastain"],
            "collection_id": None, "identity_confidence": "high", "domestic_today": 54_000_000,
            "domestic_yesterday": 53_000_000, "worldwide_today": 97_200_000, "worldwide_yesterday": 96_100_000,
            "dom_stale_days": 0, "ww_stale_days": 0}
    return {**base, **kw}


# ── the adjustment ──────────────────────────────────────────────────────

def test_the_ticket_price_table_is_sourced_year_by_year():
    t = inflation.table()
    assert inflation.reference() == (2024, 11.31) and "11.31" in t["reference"]["source"]
    assert inflation.price(1969) == 1.44 and inflation.price(1990) == 4.23 and inflation.price(2001) == 5.66
    assert all(row["source"] in t["sources"] for row in t["prices"].values())
    assert inflation.price(1938) is None and inflation.factor(1938) is None       # nothing verified before 1955


def test_a_1969_gross_is_restated_with_the_ticket_price_ratio():
    assert inflation.adjust(102_308_889, 1969) == round(102_308_889 * 11.31 / 1.44) and inflation.adjust(1, 1938) is None


def test_apply_restates_classics_keeps_the_original_and_leaves_modern_films_alone():
    corpus = [classic(), classic(tmdb_id=2, title="Modern Hit", release_year="2019", domestic_lifetime=300_000_000),
              classic(tmdb_id=3, title="Snow White", release_year="1938")]
    out = inflation.apply(corpus, TODAY)
    butch, hit, snow = corpus
    assert out["cutoff_year"] == 2001 and out["adjusted"] == 1 and out["unavailable"] == 1
    assert butch["era"] == "classic" and butch["domestic_nominal"] == 102_308_889
    assert butch["domestic_lifetime"] == round(102_308_889 * 11.31 / 1.44) > 800_000_000
    assert butch["adjusted"] == {"factor": 7.8542, "price_then": 1.44, "price_now": 11.31, "year_now": 2024, "release_year": 1969}
    assert hit.get("era") is None and hit["domestic_lifetime"] == 300_000_000      # 2019: not 25 years old
    assert reported(snow, "domestic") is None and reported(snow, "worldwide") is None  # no verified price: no comparison
    before = copy.deepcopy(corpus)
    inflation.apply(corpus, TODAY)
    assert corpus == before                                                          # idempotent


def test_the_card_sentence_says_what_was_done_and_by_how_much():
    c = classic()
    inflation.apply([c], TODAY)
    note = inflation.method_note(c, "domestic")
    assert note.startswith("Butch Cassidy and the Sundance Kid (1969): $102.3M domestic at release = about $803.6M in 2024 dollars")
    assert "US average ticket $1.44 in 1969 vs $11.31 in 2024" in note and "approximate" not in note
    assert "Worldwide is approximate" in inflation.method_note(c)                    # worldwide uses US prices


# ── the comparison itself ───────────────────────────────────────────────

def test_a_horror_film_at_97_million_is_no_longer_chasing_butch_cassidy():
    corpus = [classic()]
    inflation.apply(corpus, TODAY)
    r = chase.detect(modern(), corpus, build_index(corpus), "2026-10-30", "2026-10-01")
    assert not [e for e in r.events if e.axis == "worldwide"]


def test_a_modern_film_chasing_a_classic_in_its_own_genre_is_a_modern_vs_classic_story():
    western = classic(domestic_lifetime=40_000_000, worldwide_lifetime=41_000_000)     # $41M in 1969 = $322M today
    corpus = [western]
    inflation.apply(corpus, TODAY)
    film = modern(title="Dust", canonical_title="Dust", genres=["Western", "Drama"], worldwide_today=300_000_000,
                  worldwide_yesterday=297_000_000)
    r = chase.detect(film, corpus, build_index(corpus), "2026-10-30", "2026-10-01")
    (e,) = [x for x in r.events if x.axis == "worldwide"]
    assert e.candidate["era"] == "classic" and "classic_same_genre" in e.evidence["categories"]
    assert "classic_era" in e.reason_codes and e.strength > 0.4
    assert "in 2024 dollars" in e.reason and "US average ticket $1.44" in e.reason


def test_overtake_of_a_classic_carries_the_era():
    c = classic(domestic_lifetime=20_000_000, worldwide_lifetime=20_000_000)           # = $157M today
    corpus = [c]
    inflation.apply(corpus, TODAY)
    film = modern(domestic_today=158_000_000, domestic_yesterday=156_000_000)
    cand = {"kind": "just_passed", "axis": "domestic", "comp": corpus[0], "angle_categories": ["unexpected_giant_killer"],
            "threshold_value": corpus[0]["domestic_lifetime"]}
    (e,) = overtake.detect(film, [cand], corpus, "2026-10-30", "2026-10-01").events
    assert e.candidate["era"] == "classic" and "in 2024 dollars" in e.reason and "classic_era" in e.reason_codes


# ── the card ────────────────────────────────────────────────────────────

def _card(c_extra=None):
    comp = classic(domestic_lifetime=40_000_000, worldwide_lifetime=41_000_000)
    inflation.apply([comp], TODAY)
    film = {**modern(), "tmdb_id": 9, "title": "Dust"}
    cand = {"kind": "about_to_pass", "axis": "worldwide", "comp": comp, "strategy": "chase", "rank": 1, "event_id": "e",
            "era": "classic", "rationale": "Chase · $1.0M (0.3%) behind X · sources: tn_daily 2026-10-30", **(c_extra or {})}
    payload = {"active_films": [{**film, "candidates": [cand]}]}
    return cards.build(payload, {"source_dates": {"daily_chart": "2026-10-30"}, "weekend_strategies": {"weekend_start": "2026-10-23"}}, {}, 4)


LINKED = {"link": {"linked": True, "kinds": ["genre"], "detail": {"genre": "Western"}, "demand": [], "text": "Link: same main genre (Western)."}}


def test_the_card_is_labelled_modern_vs_classic_and_shows_both_sets_of_dollars():
    (card,) = _card(LINKED)
    assert card["label"] == "Modern vs. Classic"
    line = card["numbers"][1]
    assert "$40.0M domestic · $41.0M worldwide at release" in line and "in 2024 dollars" in line and "compared in 2024 dollars" in line


def test_a_classic_with_no_link_keeps_its_plain_label_but_still_shows_adjusted_dollars():
    (card,) = _card({"link": {"linked": False, "kinds": [], "detail": {}, "demand": [], "text": "Link: none found (notable film only)."}})
    assert card["label"] == "Chase" and "in 2024 dollars" in card["numbers"][1]


def test_a_plain_chase_keeps_its_label():
    (card,) = _card({"era": None})
    assert card["label"] == "Chase"


def test_slack_says_the_classic_is_in_todays_dollars_and_what_it_grossed_at_release(monkeypatch):
    from faramir import slack_post
    sent = {}
    monkeypatch.setattr(slack_post, "_post_message", lambda token, ch, blocks, **kw: sent.update(blocks=blocks))
    comp = classic(domestic_lifetime=40_000_000, worldwide_lifetime=41_000_000)
    inflation.apply([comp], TODAY)
    pick = {"headline": "H", "angle": "A", "axis": "worldwide", "kind": "about_to_pass", "card_label": "Modern vs. Classic",
            "active_title": "Dust", "comp_title": comp["title"], "sources": []}
    slack_post.post_pick("t", "c", pick, {"domestic_today": 5e7, "worldwide_today": 3e8}, comp)
    text = " ".join(f["text"] for b in sent["blocks"] if b.get("fields") for f in b["fields"])
    assert "lifetime, in 2024 dollars" in text and "(was $40.0M)" in text and "(was $41.0M)" in text


# ── the daily floor ─────────────────────────────────────────────────────

def _titled():
    return {"title": "T", "title_verdict": "ok"}


def test_floor_fillers_are_titled_only_while_the_day_is_short_of_two_cards():
    strong, weak = {**_titled(), "floor_fill": False}, {"floor_fill": True}
    assert config.DAILY_CARD_FLOOR == 2
    assert cards.skip_reason(weak, [strong, weak], stop_after=4) is None            # one card: the filler is needed
    assert "floor is already met" in cards.skip_reason(weak, [strong, {**_titled()}, weak], stop_after=4)
    assert cards.skip_reason({"floor_fill": False}, [strong, _titled()], stop_after=4) is None   # real stories still run
    assert "slots are full" in cards.skip_reason({"floor_fill": False}, [_titled()] * 4, stop_after=4)
    assert cards.skip_reason(weak, [weak], stop_after=None) is None                  # the harness titles everything


def test_floor_fillers_come_after_every_real_story():
    film = {**modern(), "tmdb_id": 9, "title": "Dust"}
    mk = lambda rank, fill: {"kind": "about_to_pass", "axis": "worldwide", "comp": None, "strategy": "chase", "rank": rank,   # noqa: E731
                             "event_id": f"e{rank}", "floor_fill": fill, "rationale": "Chase · x", "threshold_value": 1}
    payload = {"active_films": [{**film, "candidates": [mk(1, True)]}, {**film, "tmdb_id": 10, "candidates": [mk(2, False)]}]}
    out = cards.build(payload, {"source_dates": {"daily_chart": "2026-10-30"}, "weekend_strategies": {}}, {}, 4)
    assert [c["floor_fill"] for c in out] == [False, True]


def test_the_reason_names_why_the_comparison_film_is_notable():
    from faramir.strategies.common import meaningful
    c = classic(is_best_picture=True, is_classic=True)
    assert meaningful(["unexpected_giant_killer"], None, c) == ["a Best Picture winner and a 1969 classic"]
    assert meaningful(["unexpected_giant_killer"], None, classic(worldwide_lifetime=700_000_000, adjusted={"year_now": 2024})) \
        == ["grossed over $500M worldwide in today's dollars"]
    assert meaningful(["unexpected_giant_killer"], None) == ["a notable film (Best Picture, classic or $500M+ worldwide)"]


# ── the link: people, genre, themes and MovieWeb demand ──────────────────

def _ev(film, comp, cats, strength=0.6, strategy="chase"):
    return SimpleNamespace(strategy=strategy, film={"tmdb_id": film["tmdb_id"], "title": film["title"]}, strength=strength,
                           reason_codes=[], candidate={"comp": comp, "angle_categories": cats})


def _signal(lift, articles=12, tier="high"):
    return {"is_signal": True, "tier": tier, "lift": lift, "articles": articles}


def test_a_shared_actor_is_a_link_and_the_text_names_them():
    comp = classic(lead_cast=["Paul Newman", "Robert Redford"])
    film = modern(lead_cast=["Robert Redford"])
    link = links.build(film, comp, ["creative_head_to_head_cast"])
    assert link["linked"] and link["kinds"] == ["cast"] and "shares Robert Redford" in link["text"]


def test_same_main_genre_and_franchise_and_director_are_links():
    comp = classic(director="Rob Savage", collection_id=5)
    link = links.build(modern(collection_name="Other"), comp, ["franchise_sibling", "classic_same_genre"])
    assert set(link["kinds"]) == {"franchise", "director", "genre"}


def test_a_notable_film_alone_is_not_a_link():
    link = links.build(modern(), classic(), ["unexpected_giant_killer"])
    assert not link["linked"] and "notable film only" in link["text"]


def test_themes_need_two_specific_shared_keywords_and_ignore_generic_ones():
    f, c = modern(), classic()
    assert links.build(f, c, [], film_kw=["haunted house", "sequel"], comp_kw=["haunted house", "sequel"])["linked"] is False
    link = links.build(f, c, [], film_kw=["haunted house", "child in peril", "sequel"], comp_kw=["Haunted House", "child in peril"])
    assert link["kinds"] == ["theme"] and link["detail"]["theme"] == ["haunted house", "child in peril"]


def test_movieweb_demand_for_the_classics_talent_is_named_with_the_evidence():
    comp = classic(lead_cast=["Brad Pitt", "Nobody"])
    sig = {"Brad Pitt": _signal(3.6, 11), "Nobody": {"is_signal": False, "tier": "low", "lift": 0.5, "articles": 4}}
    link = links.build(modern(), comp, ["classic_same_genre"], sig)
    assert [d["name"] for d in link["demand"]] == ["Brad Pitt"]
    assert "Brad Pitt (3.6x MovieWeb News baseline, 11 articles)" in link["text"]


def test_demand_is_never_invented_when_there_is_no_signal():
    link = links.build(modern(), classic(lead_cast=["Brad Pitt"]), ["classic_same_genre"], {})
    assert link["demand"] == [] and "MovieWeb demand" not in link["text"]


def test_enrich_boosts_linked_and_demand_stories_and_leaves_floor_fillers_alone():
    comp = classic(lead_cast=["Brad Pitt"])
    film = modern(tmdb_id=9)
    linked = _ev(film, comp, ["classic_same_genre"])
    unlinked = _ev(film, comp, ["unexpected_giant_killer"])
    filler = _ev(film, comp, ["classic_same_genre"], strength=0.3)
    other = _ev(film, {**comp, "era": None}, ["classic_same_genre"])
    for e in (linked, unlinked, filler, other):
        e.candidate["comp"] = e.candidate["comp"] if e is other else {**comp, "era": "classic"}
    n = links.enrich([linked, unlinked, filler, other], {9: film}, lambda names: {"Brad Pitt": _signal(3.6)})
    assert n == 3 and linked.strength == 0.8 and unlinked.strength == 0.7 and filler.strength == 0.3
    assert "link:genre" in linked.reason_codes and "mw_demand" in linked.reason_codes and "link:none" in unlinked.reason_codes
    assert "link" not in other.candidate


def test_a_failed_keyword_lookup_is_no_theme_link_not_a_crash():
    comp = {**classic(), "era": "classic"}
    film = modern(tmdb_id=9)
    e = _ev(film, comp, ["classic_same_genre"])
    def boom(_):
        raise RuntimeError("tmdb down")
    assert links.enrich([e], {9: film}, None, boom) == 1 and e.candidate["link"]["kinds"] == ["genre"]
