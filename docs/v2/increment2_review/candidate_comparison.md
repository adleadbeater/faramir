# Increment 2: candidate-set comparison

Base commit for run A: `f2a0ce4`. Run date 2026-10-22. Probes: 250 (10 real chart films from 2026-10-21 plus a 240-level synthetic sweep). All probes use neutral metadata (no franchise/director/cast links), so differences come from the corpus, not the active film.

- Corpus v2 health: **OK**; usable 2007 of 2011; exclusions {'stale': 4}
- Code parity (A original code vs B current code, both on v1 corpus): **IDENTICAL** — 2038 candidates each
- Candidates: A (old) 2038, C (new) 2025; differences 374

## Differences by cause (A → C)

| Cause | Count |
|---|---|
| identity_corrected | 355 |
| excluded:stale | 14 |
| newly_surfaced_after_rescoring_or_cap | 3 |
| not_surfaced_after_rescoring_or_cap | 2 |

Missing-data handling causes **no** candidate differences by itself: the v1 index already skipped zero grosses, and v2 makes that explicit (`None`/`missing`).

## Real chart films (2026-10-21)

| Probe | Axis/kind | Old comp | New comp | Cause |
|---|---|---|---|---|
| Resident Evil | domestic/just_passed | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) | identity_corrected |
| Spider-Man: Brand New Day | domestic/just_passed | Spider-Man: Brand New Day (2026) |  | excluded:stale (S2_title_on_chart_after_gross_date) |

## Identity corrections seen in the sweep (first 30)

| Probe | Old comp | New comp |
|---|---|---|
| chart:Resident Evil | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) |
| sweep:domestic:103694961 | Godzilla x Kong: The New Empire (2024) | Godzilla vs. Kong (2021) |
| sweep:domestic:105814585 | Scream 7 (2026) | Scream (1996) |
| sweep:domestic:107977535 | The Death of Robin Hood (2026) | Robin Hood (2010) |
| sweep:domestic:107977535 | Pain Hustlers (2023) | Hustlers (2019) |
| sweep:domestic:110184699 | Captain Underpants: The First Epic Movie (2017) | Epic (2013) |
| sweep:domestic:112436978 | Creed III (2023) | Creed (2015) |
| sweep:domestic:117080595 | Magic Mike's Last Dance (2023) | Magic Mike (2012) |
| sweep:domestic:124408071 | Wayne's World 2 (1993) | Wayne's World (1992) |
| sweep:domestic:129546091 | The Sheep Detectives (2026) | Bee Movie (2007) |
| sweep:domestic:132194136 | The Lord of the Rings: The Fellowship of the Ring (2001) | The Ring (2002) |
| sweep:domestic:132194136 | Borat Subsequent Moviefilm: Delivery of Prodigious Bribe to American Regime for Make Benefit Once Glorious Nation of Kazakhstan (2020) | Borat: Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan (2006) |
| sweep:domestic:132194136 | Ghostbusters: Afterlife (2021) | Ghostbusters (2016) |
| sweep:domestic:134896310 | Tomb Raider (2018) | Lara Croft: Tomb Raider (2001) |
| sweep:domestic:134896310 | The Incredible Hulk (2008) | Hulk (2003) |
| sweep:domestic:134896310 | Avatar Aang: The Last Airbender (2026) | The Last Airbender (2010) |
| sweep:domestic:137653718 | Superman (2025) | Superman (1978) |
| sweep:domestic:137653718 | Madagascar 3: Europe's Most Wanted (2012) | Wanted (2008) |
| sweep:domestic:143338780 | Kung Fu Panda 2 (2011) | Kung Fu Panda 3 (2016) |
| sweep:domestic:146268761 | xXx: Return of Xander Cage (2017) | xXx (2002) |
| sweep:domestic:146268761 | Kung Fu Panda 2 (2011) | Kung Fu Panda 3 (2016) |
| sweep:domestic:149258633 | Puss in Boots: The Last Wish (2022) | Puss in Boots (2011) |
| sweep:domestic:149258633 | The Santa Clause 2 (2002) | The Santa Clause (1994) |
| sweep:domestic:149258633 | Lilo & Stitch (2025) | Lilo & Stitch (2002) |
| sweep:domestic:149258633 | Taken 3 (2014) | Taken (2008) |
| sweep:domestic:149258633 | Paul Blart: Mall Cop 2 (2015) | Paul Blart: Mall Cop (2009) |
| sweep:domestic:149258633 | Meg 2: The Trench (2023) | The Meg (2018) |
| sweep:domestic:155422974 | Cars 2 (2011) | Cars 3 (2017) |
| sweep:domestic:158599968 | The Fantastic 4: First Steps (2025) | Fantastic Four (2005) |
| sweep:domestic:158599968 | Fast & Furious Presents: Hobbs & Shaw (2019) | Fast & Furious (2009) |

Full list: `docs/v2/increment2_review/candidate_differences.csv`.
