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
    return brief_mod.build(film, cand, {"genres": ["Horror", "Thriller"], "director": "James Watkins", "cast": ["A B"]},
                           "2026-10-27", "2026-10-23", ["https://tn"], POOL, 8_000, [])


def test_fact_sheet_states_status_figures_and_who_may_be_named():
    b = mk(TAKEOVER)
    assert "STATUS: HAPPENED" in b.fact_sheet and "$24.0M" in b.fact_sheet and "19% ahead of No. 2" in b.fact_sheet
    assert "collection status" not in b.fact_sheet and "'Street Fighter'" in b.fact_sheet
    assert b.user_message().startswith("Clayface takes No. 1 at the box office | ")
    assert mk(CHASE).status == "anticipated"


def test_check_allows_evidence_backed_strong_wording_and_refuses_the_rest():
    b = mk(TAKEOVER)
    assert check("DC's Gritty Thriller Officially Takes No. 1 at the Box Office With $24M", b).ok
    assert check("Clayface Dethrones 'Street Fighter' at the Weekend Box Office", b).ok          # rival on the sheet
    assert not check("Clayface Takes No. 2 at the Box Office", b).ok
    assert not check("Clayface Opens to $40M at the Box Office", b).ok
    assert not check("Clayface Beats 'The Dark Knight' at the Box Office", b, ["The Dark Knight"]).ok
    assert not check("Clayface Sets an All-Time Record", b).ok
    assert not check("Clayface — No. 1", b).ok and not check("x" * 86, b).ok
    c = mk(CHASE)
    assert check("Resident Evil Is About to Pass The Sting at the Domestic Box Office", c).ok
    assert not check("Resident Evil Officially Passes The Sting", c).ok
    assert check("Clayface Nearly Tops the Box Office", b).reasons == ["anti_pattern: almost/nearly/could"]


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
                       {"mechanism": "GENRE", "title": "DC's Gritty Thriller Officially Takes No. 1 at the Box Office"},
                       {"mechanism": "STAR-LED", "title": "Clayface Dethrones 'Street Fighter' at the Weekend Box Office"}])
    t = aragorn.write(mk(TAKEOVER), fake, [])
    assert t.title.startswith("DC's Gritty") and t.alternative.startswith("Clayface Dethrones")
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
    assert not check("DCU's First Sci-Fi Thriller Officially Tops the Box Office", b).ok
    assert not check("Clayface Officially Takes No. 1 in Its Opening Weekend", b).ok
    opening = mk({**TAKEOVER, "rationale": "Takeover · Clayface is No. 1 … in its opening weekend."})
    assert opening.opening and check("Clayface Officially Takes No. 1 in Its Opening Weekend", opening).ok


def test_an_over_length_option_is_trimmed_then_rechecked():
    class TrimClient(FakeClient):
        def create(self, **kw):
            self.calls.append(kw)
            if kw["max_tokens"] == 200:
                return SimpleNamespace(content=[SimpleNamespace(text='{"title": "Clayface Officially Takes No. 1 at the Box Office"}')],
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=20))
            return super().create(**kw)
    long = "Clayface, the Gritty New Thriller From the Studio, Officially Takes No. 1 at the Weekend Box Office"
    t = aragorn.write(mk(TAKEOVER), TrimClient([{"mechanism": "MILESTONE", "title": long}]), [])
    assert t.title == "Clayface Officially Takes No. 1 at the Box Office" and t.options[0]["original_title"] == long
    assert t.usage["input_tokens"] == 9100
