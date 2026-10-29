"""Title stage: MovieWeb evidence (Power Words re-measured on the desk, subject demand), the fact
sheet, the fact check and the Aragorn-method writer (with a fake client: no paid calls)."""

import json
from datetime import datetime
from types import SimpleNamespace

from faramir.analytics.cerebro_snapshot import Article
from faramir.titles import aragorn, brief as brief_mod, data
from faramir.titles.check import check


def art(title, sessions, ctype="News", tags=(), pri="", dc=0):
    return Article(title, "u", datetime(2026, 8, 1), "Publish", "MW", "Movies", "Movies", pri, list(tags), ctype,
                   "Regular", sessions, None, dc, None)


POOL = [art("Henry Cavill's Action Thriller Officially Passes $100M", 30_000, tags=["Box Office", "Henry Cavill"]),
        art("Action Thriller Sequel Sets New Release", 20_000, tags=["Thriller"]),
        art("Officially Confirmed Streaming Hit", 10_000, ctype="Flash News"),
        art("Officially Something", 400, tags=["Box Office"])]


def test_power_words_are_remeasured_on_the_desk_and_filtered():
    words = [data.Phrase("Officially", 1158, 21_469), data.Phrase("Reveals", 114, 4_024),
             data.Phrase("Streaming Hit", 108, 61_628), data.Phrase("Action Thriller", 765, 42_205)]
    data.measure_phrases(words, POOL)
    off = words[0]
    assert (off.desk_n, off.desk_sa) == (3, round((30_000 + 10_000 + 400) / 3))
    block = data.power_words_block(words, 8_000, min_n=1)
    assert "Officially: desk S/A" in block and "Action Thriller" in block
    assert "Reveals: desk" not in block and "Streaming Hit" not in block      # avoid list; other format


def test_snapshot_power_words_load(tmp_path):
    p = tmp_path / "pw.tsv"
    p.write_text("# snapshot\nphrase\tuse\tsessions\tsa\tfr\thh\nOfficially\t1158\t24861201\t21469\t8\t22\nOfficially\t1\t1\t1\t0\t0\n")
    words, source = data.load_power_words(None, str(p))
    assert [w.phrase for w in words] == ["Officially"] and source.startswith("power_words_sheet:snapshot")


def test_subject_demand_uses_exact_tags_and_flags_no_coverage():
    d = data.demand(POOL, ["Henry Cavill", "Cavill", "Nobody"], ["Box Office"])
    assert [s["subject"] for s in d["subjects"]] == ["Henry Cavill"] and d["no_coverage"] == ["Cavill", "Nobody"]
    assert d["genres"][0]["articles"] == 2 and d["genres"][0]["small_sample"]
    assert "Henry Cavill" in data.demand_block(d, 8_000)


FILM = {"tmdb_id": 1, "title": "Clayface", "domestic_today": 30e6, "worldwide_today": None}
TAKEOVER = {"kind": "event", "strategy": "takeover", "status": "confirmed", "axis": "weekend",
            "event_values": {"rank": 1, "weekend_gross": 24_000_000, "theaters": 3458, "previous_leader": "Street Fighter",
                             "margin_over_second": 0.19, "collection_status": "confirmed"},
            "rationale": "Takeover · Clayface is No. 1 · sources: x"}
CHASE = {"kind": "about_to_pass", "strategy": "chase", "axis": "domestic", "threshold_value": 156e6,
         "comp": {"title": "The Sting", "release_year": "1973", "domestic_lifetime": 156_000_000, "worldwide_lifetime": 156_000_000},
         "rationale": "Chase · $1.7M behind"}


def mk(cand, film=FILM):
    return brief_mod.build(film, cand, {"genres": ["Horror", "Thriller"], "director": "James Watkins", "cast": ["A B"],
                                       "keywords": ["gore", "dc universe (dcu)"], "companies": ["DC Studios"],
                                       "certification": "R"},
                           "2026-10-27", "2026-10-23", ["https://tn"], POOL, 8_000, [])


def test_fact_sheet_states_status_figures_and_who_may_be_named():
    b = mk(TAKEOVER)
    assert "STATUS: HAPPENED" in b.fact_sheet and "$24.0M" in b.fact_sheet and "19% ahead of No. 2" in b.fact_sheet
    assert "collection status" not in b.fact_sheet and "'Street Fighter'" in b.fact_sheet
    assert b.user_message().startswith("Clayface takes No. 1 at the box office | ")
    assert mk(CHASE).status == "anticipated"


def test_check_allows_evidence_backed_strong_wording_and_refuses_the_rest():
    b = mk(TAKEOVER)
    assert check("DC's Brutal Thriller Officially Takes No. 1 From 'Street Fighter' at the Box Office With $24M", b).ok
    assert check("Clayface Dethrones 'Street Fighter' at the Weekend Box Office", b).ok          # rival on the sheet
    assert not check("Clayface Takes No. 2 at the Box Office", b).ok
    assert not check("Clayface Opens to $40M at the Box Office", b).ok
    assert not check("Clayface Beats 'The Dark Knight' at the Box Office", b, ["The Dark Knight"]).ok
    assert not check("Clayface Sets an All-Time Record", b).ok
    assert not check("Clayface — No. 1", b).ok and not check("x" * 96, b).ok
    c = mk(CHASE)
    assert check("Resident Evil Is About to Pass The Sting at the Domestic Box Office", c).ok
    assert not check("Resident Evil Officially Passes The Sting", c).ok
    assert "anti_pattern: almost/nearly/could" in check("Clayface Nearly Tops the Box Office", b).reasons


class FakeClient:
    def __init__(self, options):
        self.options, self.calls = options, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.options))],
                               usage=SimpleNamespace(input_tokens=9000, output_tokens=800))


def test_writer_uses_aragorns_prompt_and_keeps_only_checked_options():
    fake = FakeClient([{"mechanism": "MILESTONE", "title": "Clayface Takes No. 2 at the Box Office"},
                       {"mechanism": "GENRE", "title": "DC's Brutal Thriller Officially Takes No. 1 From 'Street Fighter' at the Box Office"},
                       {"mechanism": "STAR-LED", "title": "Clayface Dethrones 'Street Fighter' at the Weekend Box Office"}])
    t = aragorn.write(mk(TAKEOVER), fake, [])
    assert t.title.startswith("DC's Brutal") and t.alternative.startswith("Clayface Dethrones")
    sent = fake.calls[0]
    assert "You are aragorn" in sent["system"] and "Box-office stories from Faramir" in sent["system"]
    assert sent["model"] == aragorn.MODEL and "ARTICLE BODY" in sent["messages"][0]["content"]
    assert not t.options[0]["check"]["ok"]


def test_writer_reports_a_kill_and_never_raises():
    assert aragorn.write(mk(TAKEOVER), FakeClient([{"mechanism": "KILL", "tier": 0, "logic": "soft"}]), []).verdict == "kill"

    class Boom:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    assert aragorn.write(mk(TAKEOVER), Boom(), []).verdict == "error"


def test_firsts_and_openings_need_the_fact_sheet():
    b = mk(TAKEOVER)
    assert not check("DCU's First Sci-Fi Thriller Officially Tops 'Street Fighter' at the Box Office", b).ok
    assert not check("Clayface Officially Takes No. 1 From Street Fighter in Its Opening Box Office Weekend", b).ok
    opening = mk({**TAKEOVER, "rationale": "Takeover · Clayface is No. 1 … in its opening weekend."})
    assert opening.opening and check("Clayface Officially Takes No. 1 From Street Fighter in Its Opening Box Office Weekend", opening).ok


def test_an_over_length_option_is_trimmed_then_rechecked():
    class TrimClient(FakeClient):
        def create(self, **kw):
            self.calls.append(kw)
            if kw["max_tokens"] == 200:
                return SimpleNamespace(content=[SimpleNamespace(text='{"title": "Clayface Officially Takes No. 1 From Street Fighter at the Box Office"}')],
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=20))
            return super().create(**kw)
    long = "Clayface, the Shapeshifting Supervillain Movie From the Studio Behind It All, Officially Takes No. 1 This Weekend"
    t = aragorn.write(mk(TAKEOVER), TrimClient([{"mechanism": "MILESTONE", "title": long}]), [])
    assert t.title == "Clayface Officially Takes No. 1 From Street Fighter at the Box Office" and t.options[0]["original_title"] == long
    assert t.usage["input_tokens"] == 9100


def test_the_gap_to_the_comparison_is_a_verified_figure():
    film = {**FILM, "domestic_today": 154_300_000}
    b = mk(CHASE, film)
    assert "Gap still to go: $1.7M" in b.fact_sheet
    assert check("Resident Evil Is $1.7M Away From Passing The Sting at the Box Office", b).ok


def test_discovery_figures_are_stated_as_having_happened():
    disc = {"kind": "event", "strategy": "discovery", "status": "anticipated", "axis": "weekend",
            "event_values": {"weekend_gross": 19_500_000, "previous_weekend_gross": 15_600_000},
            "rationale": "Discovery · up 25%"}
    assert mk(disc).status == "confirmed" and "STATUS: HAPPENED" in mk(disc).fact_sheet


GENRE_POOL = ([art(f"Star's Action Thriller Story {i}", 25_000) for i in range(12)]
              + [art(f"Big Thriller News {i}", 9_000) for i in range(12)]
              + [art(f"Action Adventure Hit {i}", 40_000) for i in range(2)]
              + [art(f"Crime Thriller Case {i}", 3_000) for i in range(6)]
              + [art(f"Western Return {i}", 12_000) for i in range(6)]
              + [art(f"Action Sequel Update {i}", 4_000) for i in range(20)])


def test_genre_wording_is_ranked_by_movieweb_performance_among_accurate_options():
    from faramir.titles.data import genre_candidates, genre_options
    assert "Action Thriller" in genre_candidates(["Adventure", "Thriller"])        # Adventure → Action relabel
    assert "Horror" not in genre_candidates(["Adventure", "Thriller"])             # never an inaccurate genre
    opts = genre_options(["Adventure", "Thriller"], GENRE_POOL, [])
    assert opts[0]["phrase"] == "Action Thriller"                                  # 2 big uses don't beat 12
    comp = {**CHASE["comp"], "genres": ["Crime", "Thriller", "Western"], "is_best_picture": True}
    b = brief_mod.build(FILM, {**CHASE, "comp": comp}, {"genres": ["Adventure", "Thriller"]}, "2026-10-27", None, [],
                        GENRE_POOL, 8_000, [])
    assert b.film_genre_phrase == "Action Thriller" and b.comp_masked_as == "a Best Picture-Winning Thriller"
    assert "GENRE WORDING" in b.user_message() and "REQUIRED FOR THIS STORY" in b.user_message()
    assert "genres: Crime, Thriller, Western · Best Picture winner" in b.fact_sheet
    assert check("Clayface's Action Thriller Eyes a Best Picture-Winning Thriller at the Box Office", b).ok
    assert not check("Clayface's Action-Adventure Thriller Eyes 'The Sting' at the Box Office", b).ok
    assert not check("Clayface's Thriller Eyes 'The Sting' at the Box Office", b).ok
    assert not check("Clayface's Action Thriller Eyes a Best Picture Winner at the Box Office", b).ok
    assert check("Clayface Is About to Pass 'The Sting' at the Box Office", b).ok      # no genre named: fine


def test_deterministic_shortening_keeps_protected_words():
    t = "Anne Hathaway's Psychological Thriller Is Closing In On a Best Picture-Winning Crime Thriller Worldwide"
    short = aragorn.shorten(t, 95)
    assert len(short) <= 95 and "Best Picture-Winning Crime Thriller" in short and "Anne Hathaway" in short
    assert aragorn.shorten("Brad Pitt's Action Thriller Officially Passes X at the Worldwide Box Office", 60).count("Action Thriller") == 1


def test_house_rules_box_office_takeover_leader_and_never_blank():
    b = mk(TAKEOVER)
    assert any(r.startswith("box_office_missing") for r in check("Clayface Takes No. 1 From Street Fighter", b).reasons)
    assert any(r.startswith("takeover_needs_previous_leader") for r in check("Clayface Takes No. 1 at the Box Office", b).reasons)
    assert "Name the film it replaced at No. 1: 'Street Fighter'" in b.user_message() and "\"Box Office\"" in b.user_message()
    long = ("Clayface, the Shapeshifting Supervillain Movie From the Studio Behind It All, Officially Takes No. 1 From "
            "Street Fighter at the Box Office")
    class NoTrim(FakeClient):
        def create(self, **kw):
            if kw["max_tokens"] == 200:
                raise RuntimeError("trim unavailable")
            return super().create(**kw)
    t = aragorn.write(b, NoTrim([{"mechanism": "MILESTONE", "title": long}]), [])
    assert t.title == long and t.verdict == "over_length" and "editor to trim" in t.note


def test_verbs_are_ranked_by_movieweb_titles_within_what_is_true():
    from faramir.titles.data import verb_group, verb_options
    pool = ([art(f"Film Eyes Record {i}", 12_000) for i in range(5)] + [art(f"Film Set to Win {i}", 9_000) for i in range(4)]
            + [art(f"Film Nears Goal {i}", 2_000) for i in range(6)] + [art("Film Is About to Pass X", 90_000)])
    opts = verb_options(verb_group("chase", "anticipated", True), pool)
    assert [o["phrase"] for o in opts if o["allowed"]] == ["Eyes", "Is Set to Pass"]   # one big use isn't proven
    assert next(o for o in opts if o["phrase"] == "Is About to Pass")["basis"] == "unproven"
    assert verb_group("takeover", "confirmed", False) == "number_one" and verb_group("overtake", "confirmed", True) == "pass"
    assert verb_group("streak", "confirmed", False) is None


def test_weak_verb_is_rejected_and_repairable():
    pool = [art(f"Film Eyes Record {i}", 12_000) for i in range(5)] + [art(f"Film Set to Win {i}", 9_000) for i in range(4)]
    b = brief_mod.build(FILM, CHASE, {"genres": ["Horror"]}, "2026-10-27", None, ["https://tn"], pool, 8_000, [])
    c = check("Resident Evil Is About to Pass The Sting at the Domestic Box Office", b)
    assert not c.ok and c.reasons[0].startswith("verb_weak: use 'Eyes' or 'Is Set to Pass'")
    assert check("Resident Evil Eyes The Sting at the Domestic Box Office", b).ok
    assert "Verb: use one of the strongest proven verbs" in b.user_message() and "Not: " in b.user_message()


def test_descriptors_need_data_behind_them():
    b = mk(TAKEOVER)
    assert any(r.startswith("descriptor_unsupported: drop 'Raunchy'") for r in
               check("Clayface's Raunchy Thriller Takes No. 1 From 'Street Fighter' at the Box Office", b).reasons)
    assert check("DCU's R-Rated Thriller Takes No. 1 From 'Street Fighter' at the Box Office", b).ok
    assert not check("Marvel's Thriller Takes No. 1 From 'Street Fighter' at the Box Office", b).ok
    c = mk(CHASE)    # The Sting: $156M domestic, $156M worldwide
    assert check("Resident Evil Is About to Pass a Classic Hit at the Domestic Box Office", c).ok
    assert not check("Resident Evil Is About to Pass a Major Hit at the Domestic Box Office", c).ok
    assert "Supported: Clayface: " in b.user_message() and "DCU" in b.user_message()


def test_a_documentary_is_not_a_biopic():
    facts = [data.film_facts({"genres": ["Documentary"], "keywords": ["biography"], "companies": ["A24"]})]
    assert data.unsupported_descriptors("A24's Elizabeth Holmes Biopic Gains at the Box Office", facts) == ["Biopic"]
    assert data.unsupported_descriptors("Biopic Gains", [data.film_facts({"genres": ["Drama"], "keywords": ["biography"]})]) == []
