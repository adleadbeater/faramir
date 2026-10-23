# Increment 2: candidate-set comparison (reconciled)

Base commit for run A: `f2a0ce4`. Run date 2026-10-22. Probes: 250 (10 real chart films from 2026-10-21 plus a 240-level synthetic sweep), all with neutral metadata (no franchise/director/cast links), so differences come from the corpus, not the active film.

Runs: **A** original code + v1 corpus; **B** current code + v1 corpus; **C** current code + usable v2 records.
Candidates are matched by `(probe, axis, kind, comp IMDb id)`. The IMDb id is the film's stable identity, so a relabelled record is matched to itself and counted as an identity correction, not as a removed + added story.

- Corpus v2 health: **OK**; usable 2007 of 2011; exclusions {'stale': 4}
- Code parity A vs B: **IDENTICAL** (2038 candidates each) — the code changes alone change nothing on v1 data.

## Reconciliation

| Category | Count | In A (old) | In C (new) | Counted as a difference |
|---|---|---|---|---|
| Unchanged (same identity, categories, score) | 1667 | ✓ | ✓ | no |
| Identity corrected, score unchanged | 301 | ✓ | ✓ | yes |
| Identity corrected and score changed | 54 | ✓ | ✓ | yes |
| Score/categories changed, same identity | 0 | ✓ | ✓ | yes |
| Removed: stale exclusion | 14 | ✓ | — | yes |
| Removed: no longer surfaces after corrected metadata | 2 | ✓ | — | yes |
| Added: newly surfaces after corrected metadata | 3 | — | ✓ | yes |
| **Total** | | **2038** | **2025** | **374** |

- A = 1667 + 355 + 0 + 16 = 2038
- C = 1667 + 355 + 0 + 3 = 2025
- Differences = 355 corrected + 0 rescored + 16 removed + 3 added = 374
- Net change C − A = 3 added − 16 removed = -13
- The 355 identity corrections involve 157 distinct comp records (the same record is a candidate for several probe levels).

Missing-data handling causes **no** differences by itself: the v1 index already skipped zero grosses; v2 makes that explicit.
No editorial scoring was changed; every score change comes from corrected comp metadata (e.g. release year → classic flag).

## Real chart films (2026-10-21)

| Probe | Axis/kind | Old comp | New comp | Change |
|---|---|---|---|---|
| Resident Evil | domestic/just_passed | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) | identity_corrected |
| Spider-Man: Brand New Day | domestic/just_passed | Spider-Man: Brand New Day (2026) |  | removed_stale |

## Removed: stale exclusions

| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |
|---|---|---|---|---|---|---|
| chart:Spider-Man: Brand New Day | domestic/just_passed | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (16) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:590901605 | domestic/about_to_pass | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (8) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:602980195 | domestic/about_to_pass | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (8) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:615305682 | domestic/about_to_pass | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (8) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:627883115 | domestic/just_passed | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (10) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:922249625 | domestic/about_to_pass | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (14) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:941101283 | domestic/about_to_pass | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (14) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:domestic:960338287 | domestic/just_passed | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (16) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:1693813467 | worldwide/about_to_pass | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (8) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:1742923659 | worldwide/about_to_pass | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (8) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:1793457745 | worldwide/just_passed | tt33764258 | The Odyssey (2026) |  | unexpected_giant_killer (10) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:2386816247 | worldwide/about_to_pass | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (14) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:2456019265 | worldwide/about_to_pass | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (14) → — () | stale (S2_title_on_chart_after_gross_date) |
| sweep:worldwide:2527228745 | worldwide/just_passed | tt22084616 | Spider-Man: Brand New Day (2026) |  | genre_milestone|same_origin|unexpected_giant_killer (16) → — () | stale (S2_title_on_chart_after_gross_date) |

## Removed: no longer surfaces

| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |
|---|---|---|---|---|---|---|
| sweep:domestic:114735297 | domestic/about_to_pass | tt3861390 | Dumbo (1941) |  | same_origin|unexpected_giant_killer (11) → — () | dropped below MIN_RICHNESS_TO_SURFACE or per-cell cap after the comp's corrected metadata changed its score |
| sweep:domestic:158599968 | domestic/about_to_pass | tt1502407 | Halloween (1978) |  | same_origin|unexpected_giant_killer (11) → — () | dropped below MIN_RICHNESS_TO_SURFACE or per-cell cap after the comp's corrected metadata changed its score |

## Added

| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |
|---|---|---|---|---|---|---|
| sweep:domestic:105814585 | domestic/about_to_pass | tt0066011 |  | Love Story (1970) | — () → same_origin|unexpected_giant_killer (11) | newly reaches MIN_RICHNESS_TO_SURFACE / per-cell cap after corrected metadata |
| sweep:domestic:219234624 | domestic/about_to_pass | tt4630562 |  | The Fate of the Furious (2017) | — () → same_origin|unexpected_giant_killer (9) | newly reaches MIN_RICHNESS_TO_SURFACE / per-cell cap after corrected metadata |
| sweep:worldwide:105882836 | worldwide/about_to_pass | tt0066011 |  | Love Story (1970) | — () → same_origin|unexpected_giant_killer (11) | newly reaches MIN_RICHNESS_TO_SURFACE / per-cell cap after corrected metadata |

## Score changed with corrected identity

| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |
|---|---|---|---|---|---|---|
| sweep:domestic:129546091 | domestic/just_passed | tt0389790 | The Sheep Detectives (2026) | Bee Movie (2007) | — (3) → same_origin (4) |  |
| sweep:domestic:137653718 | domestic/just_passed | tt0078346 | Superman (2025) | Superman (1978) | same_origin (4) → same_origin|unexpected_giant_killer (13) |  |
| sweep:domestic:149258633 | domestic/just_passed | tt0936501 | Taken 3 (2014) | Taken (2008) | same_origin (4) → — (3) |  |
| sweep:domestic:161841901 | domestic/just_passed | tt1502407 | Halloween (1978) | Halloween (2018) | same_origin|unexpected_giant_killer (13) → same_origin (4) |  |
| sweep:domestic:179073104 | domestic/just_passed | tt6857112 | Let Us Prey (2014) | Us (2019) | — (3) → same_origin (4) |  |
| sweep:domestic:223715988 | domestic/just_passed | tt0099653 | Ghost in the Cell (2026) | Ghost (1990) | unexpected_giant_killer (10) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:394237206 | domestic/about_to_pass | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:402295789 | domestic/about_to_pass | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:410519096 | domestic/just_passed | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:418910496 | domestic/just_passed | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:482654533 | domestic/about_to_pass | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:492520449 | domestic/about_to_pass | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:502588035 | domestic/about_to_pass | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:512861411 | domestic/just_passed | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (13) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:523344785 | domestic/about_to_pass | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:534042450 | domestic/about_to_pass | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:544958784 | domestic/just_passed | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (13) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:556098259 | domestic/just_passed | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (13) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:653814518 | domestic/about_to_pass | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:667179108 | domestic/about_to_pass | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:680816882 | domestic/just_passed | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:694733425 | domestic/just_passed | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:753303017 | domestic/about_to_pass | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (9) → genre_milestone|same_origin|unexpected_giant_killer (14) |  |
| sweep:domestic:768701246 | domestic/about_to_pass | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (9) → genre_milestone|same_origin|unexpected_giant_killer (14) |  |
| sweep:domestic:784414229 | domestic/about_to_pass | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (9) → genre_milestone|same_origin|unexpected_giant_killer (14) |  |
| sweep:domestic:800448400 | domestic/just_passed | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (11) → genre_milestone|same_origin|unexpected_giant_killer (16) |  |
| sweep:domestic:95632639 | domestic/just_passed | tt0042332 | Cinderella (2015) | Cinderella (1950) | same_origin (4) → same_origin|unexpected_giant_killer (13) |  |
| sweep:worldwide:112111750 | worldwide/just_passed | tt0393597 | The Day the Earth Stood Still (2008) | Earth (2007) | — (3) → same_origin (4) |  |
| sweep:worldwide:118707101 | worldwide/just_passed | tt0296572 | Riddick (2013) | The Chronicles of Riddick (2004) | — (3) → same_origin (4) |  |
| sweep:worldwide:122148878 | worldwide/just_passed | tt2401825 | Forever Young (1992) | Forever Young (2018) | same_origin (4) → — (3) |  |
| sweep:worldwide:1236872578 | worldwide/about_to_pass | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:1272734290 | worldwide/just_passed | tt2771200 | Beauty and the Beast (1991) | Beauty and the Beast (2017) | same_origin|unexpected_giant_killer (13) → same_origin|unexpected_giant_killer (11) |  |
| sweep:worldwide:133084610 | worldwide/just_passed | tt0425637 | Clifford the Big Red Dog (2021) | Red Cliff (2008) | same_origin (4) → — (3) |  |
| sweep:worldwide:144999394 | worldwide/just_passed | tt3741834 | Lion Fist (2026) | Lion (2016) | same_origin (4) → — (3) |  |
| sweep:worldwide:1599705413 | worldwide/about_to_pass | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:1646087048 | worldwide/about_to_pass | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:1693813467 | worldwide/just_passed | tt6105098 | The Lion King (1994) | The Lion King (2019) | same_origin|unexpected_giant_killer (13) → same_origin|unexpected_giant_killer (11) |  |
| sweep:worldwide:182250385 | worldwide/just_passed | tt0299977 | Big Hero 6 (2014) | Hero (2002) | same_origin (4) → — (3) |  |
| sweep:worldwide:187534528 | worldwide/just_passed | tt1717715 | The Monkey King: Uproar in Heaven (1961) | The Monkey King (2014) | unexpected_giant_killer (12) → — (3) |  |
| sweep:worldwide:2010676877 | worldwide/about_to_pass | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:2068974162 | worldwide/just_passed | tt4154756 | Avengers: Endgame (2019) | Avengers: Infinity War (2018) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |
| sweep:worldwide:235712986 | worldwide/just_passed | tt36733027 | Nobody 2 (2025) | Nobody (2025) | same_origin (4) → — (3) |  |
| sweep:worldwide:2833320392 | worldwide/about_to_pass | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (9) → genre_milestone|same_origin|unexpected_giant_killer (14) |  |
| sweep:worldwide:2915469289 | worldwide/about_to_pass | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (9) → genre_milestone|same_origin|unexpected_giant_killer (14) |  |
| sweep:worldwide:3000000000 | worldwide/just_passed | tt0499549 | Avatar: Fire and Ash (2025) | Avatar (2009) | same_origin|unexpected_giant_killer (11) → genre_milestone|same_origin|unexpected_giant_killer (16) |  |
| sweep:worldwide:429587826 | worldwide/just_passed | tt0120616 | Lee Cronin's The Mummy (2026) | The Mummy (1999) | — (3) → same_origin (4) |  |
| sweep:worldwide:429587826 | worldwide/just_passed | tt10218664 | Captain America: The Winter Soldier (2014) | The Captain (2019) | same_origin (4) → — (3) |  |
| sweep:worldwide:468047917 | worldwide/just_passed | tt14557302 | Man on the Moon (1999) | Moon Man (2022) | same_origin (4) → — (3) |  |
| sweep:worldwide:495582411 | worldwide/about_to_pass | tt0099653 | Ghost in the Cell (2026) | Ghost (1990) | unexpected_giant_killer (8) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:509951258 | worldwide/just_passed | tt0099653 | Ghost in the Cell (2026) | Ghost (1990) | unexpected_giant_killer (10) → same_origin|unexpected_giant_killer (11) |  |
| sweep:worldwide:718591493 | worldwide/about_to_pass | tt10627720 | Ne Zha 2 (2025) | Ne Zha (2019) | genre_milestone|unexpected_giant_killer (13) → unexpected_giant_killer (8) |  |
| sweep:worldwide:739426235 | worldwide/just_passed | tt10627720 | Ne Zha 2 (2025) | Ne Zha (2019) | genre_milestone|unexpected_giant_killer (15) → unexpected_giant_killer (10) |  |
| sweep:worldwide:805625504 | worldwide/about_to_pass | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (14) → same_origin|unexpected_giant_killer (9) |  |
| sweep:worldwide:828983698 | worldwide/just_passed | tt0145487 | Spider-Man: Brand New Day (2026) | Spider-Man (2002) | genre_milestone|same_origin|unexpected_giant_killer (16) → same_origin|unexpected_giant_killer (11) |  |

## Score changed, same identity

_None._

## Identity corrections, score unchanged (first 25)

| Probe | Axis/kind | Comp IMDb | Old | New | Old→new categories (score) | Detail |
|---|---|---|---|---|---|---|
| chart:Resident Evil | domestic/just_passed | tt1114740 | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:103694961 | domestic/just_passed | tt5034838 | Godzilla x Kong: The New Empire (2024) | Godzilla vs. Kong (2021) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:105814585 | domestic/just_passed | tt0117571 | Scream 7 (2026) | Scream (1996) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:107977535 | domestic/just_passed | tt0955308 | The Death of Robin Hood (2026) | Robin Hood (2010) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:107977535 | domestic/just_passed | tt5503686 | Pain Hustlers (2023) | Hustlers (2019) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:110184699 | domestic/just_passed | tt0848537 | Captain Underpants: The First Epic Movie (2017) | Epic (2013) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:112436978 | domestic/just_passed | tt3076658 | Creed III (2023) | Creed (2015) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:117080595 | domestic/just_passed | tt1915581 | Magic Mike's Last Dance (2023) | Magic Mike (2012) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:124408071 | domestic/just_passed | tt0105793 | Wayne's World 2 (1993) | Wayne's World (1992) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:132194136 | domestic/just_passed | tt0298130 | The Lord of the Rings: The Fellowship of the Ring (2001) | The Ring (2002) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:132194136 | domestic/just_passed | tt0443453 | Borat Subsequent Moviefilm: Delivery of Prodigious Bribe to American Regime for Make Benefit Once Glorious Nation of Kazakhstan (2020) | Borat: Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan (2006) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:132194136 | domestic/just_passed | tt1289401 | Ghostbusters: Afterlife (2021) | Ghostbusters (2016) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:134896310 | domestic/just_passed | tt0146316 | Tomb Raider (2018) | Lara Croft: Tomb Raider (2001) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:134896310 | domestic/just_passed | tt0286716 | The Incredible Hulk (2008) | Hulk (2003) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:134896310 | domestic/just_passed | tt0938283 | Avatar Aang: The Last Airbender (2026) | The Last Airbender (2010) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:137653718 | domestic/just_passed | tt0493464 | Madagascar 3: Europe's Most Wanted (2012) | Wanted (2008) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:143338780 | domestic/about_to_pass | tt2267968 | Kung Fu Panda 2 (2011) | Kung Fu Panda 3 (2016) | same_origin|unexpected_giant_killer (9) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:146268761 | domestic/just_passed | tt0295701 | xXx: Return of Xander Cage (2017) | xXx (2002) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:146268761 | domestic/just_passed | tt2267968 | Kung Fu Panda 2 (2011) | Kung Fu Panda 3 (2016) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:149258633 | domestic/about_to_pass | tt0448694 | Puss in Boots: The Last Wish (2022) | Puss in Boots (2011) | same_origin|unexpected_giant_killer (9) → same_origin|unexpected_giant_killer (9) |  |
| sweep:domestic:149258633 | domestic/just_passed | tt0111070 | The Santa Clause 2 (2002) | The Santa Clause (1994) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:149258633 | domestic/just_passed | tt0275847 | Lilo & Stitch (2025) | Lilo & Stitch (2002) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:149258633 | domestic/just_passed | tt1114740 | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) | same_origin (4) → same_origin (4) |  |
| sweep:domestic:149258633 | domestic/just_passed | tt4779682 | Meg 2: The Trench (2023) | The Meg (2018) | same_origin|unexpected_giant_killer (11) → same_origin|unexpected_giant_killer (11) |  |
| sweep:domestic:155422974 | domestic/just_passed | tt3606752 | Cars 2 (2011) | Cars 3 (2017) | same_origin (4) → same_origin (4) |  |

Full list: `docs/v2/increment2_review/candidate_differences.csv`.
