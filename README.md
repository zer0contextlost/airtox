# airtox: EPA air toxics risk estimates, 1996-2021, in one format

Every national air toxics assessment EPA has published (NATA 1996, 1999, 2002, 2005, 2011, 2014 and AirToxScreen 2017-2020), converted from Access databases, xls, xlsx and zipped CSVs into Parquet with consistent column names. Plus 2021 block-level cancer risk, which EPA did not publish.

**Data:** https://huggingface.co/datasets/zer0contextlost/airtox
**Code:** https://github.com/zer0contextlost/airtox

## Why 2021 is here

In August 2026 EPA posted 2021 block-level ambient and exposure concentrations for 177 air toxics ("2021 Multipollutant Platform", gaftp.epa.gov/Air/Multipollutant_Platform/2021/) without the risk step that AirToxScreen normally adds. This dataset applies AirToxScreen's own cancer risk method to those concentrations:

    risk per million = exposure concentration (ug/m3) x unit risk estimate (1/(ug/m3)) x 1e6

The unit risk estimates are EPA's 2020 AirToxScreen pollutant table (`ref/2020AirToxScreenPollutants.xlsx`, mutagenic adjustment already included). For 2018 and 2019 we back-calculated the values EPA actually used from its published risk and exposure tables (risk / exposure, per tract), and they match this table for every carcinogen except PAHPOM. So it is the table EPA used for 2018-2020.

**Method check.** We ran the same code on EPA's 2020 exposure concentrations and compared the result with EPA's published 2020 risk. For every block in EPA Region 1 (225,438 blocks), all 71 carcinogens except PAHPOM and all 39 source groups match EPA's per-pollutant risk files. The 99th-percentile relative error is about 1e-9. The largest is 5e-4, which comes from rounding in EPA's CSVs. See `validation/recompute_method_check_2020_R1.json`.

**What this is:** EPA's method applied to EPA's data, for screening. It is not EPA's 2021 assessment and EPA has not reviewed it. The usual AirToxScreen caveats apply: modeled estimates of lifetime risk from inhalation of outdoor air toxics, meant to find places and pollutants worth a closer look, not to describe any person's actual risk.

### PAHPOM is estimated, not computed

PAHPOM (polycyclic organic matter) is a lump of PAH groups that EPA scores with different unit risks per group. EPA publishes only the lumped concentration, so its risk can't be recomputed exactly. For 2021 we estimate it per block and per source group as:

    PAHPOM risk 2021 = PAHPOM exposure 2021 x (EPA PAHPOM risk 2020 / PAHPOM exposure 2020), same block, same source group

The 2020 ratio of the block's total is the fallback where a source group had no 2020 PAHPOM. That is mostly the two source groups that are new in 2021, `TRANSPORT` and `NP-FirePileBurns`. Nationally, about 0.3% of estimated PAHPOM risk or less uses the fallback in any region.

How good is that? There's no 2022 data to check against, so we tested the same carry-forward one year earlier. We took 2019 county ratios, applied them to 2020 exposure, and compared the result with EPA's 2020 PAHPOM risk. The median county error was 47%, and the national population-weighted PAHPOM risk came out 2.1 per million against EPA's 1.1. EPA's PAHPOM inventory roughly doubled between 2019 and 2020, so this is a hard test. Still, treat the 2021 PAHPOM column as uncertain to within a factor of about 2. PAHPOM is 3.8% of 2021 national population-weighted risk. Every 2021 block row carries `total_excl_pahpom` (exact) next to `PAHPOM` (estimate) and `total_cancer_risk_per_million` (both). File: `validation/pahpom_transfer_2019_2020_county.csv`.

### National numbers

| year | blocks | people | pop-weighted cancer risk per million | of which PAHPOM | origin |
|---|---|---|---|---|---|
| 2020 | 5,808,484 | 334,725,204 | 30.0 |  | epa_published |
| 2021 | 5,807,940 | 334,725,204 | 27.8 | 1.07 (estimate) | recomputed_2021 |

EPA cautions that years are not directly comparable: emissions inventories, models and toxicity values change between assessments.

## Noncancer hazard, 2020 and 2021 (added in v1.1)

EPA did not publish block-level noncancer results for 2020 or any noncancer results for 2021. `data/hazard/<year>/` has them for every block, using AirToxScreen's method:

    hazard quotient (HQ) = exposure concentration (ug/m3) / (RfC (mg/m3) x 1000)
    hazard index (HI) for an organ = sum of HQ over the pollutants EPA flags for that organ

RfCs and organ flags come from the same EPA 2020 pollutant table. **Method check:** we rebuilt EPA's published 2019 tract hazard indexes from EPA's 2019 exposure concentrations.
- The pollutant sets per organ match EPA exactly for all five organs EPA publishes in detail.
- Per-pollutant HQs match within EPA's rounding: 99th-percentile error about 0, largest 0.1%. Ethylene oxide is off by up to 64% in a handful of tracts at very small values.
- All 14 organ HIs fall within EPA's rounding in 100% of tracts (respiratory 99.995%).

See `validation/noncancer_method_check_2019.json`.

Files per region: `_hi` (14 organ HIs per block), `_hq_by_poll` (HQ for each of the 97 pollutants with an RfC), `_resp_hi_by_src` (respiratory HI by source group). Tract and county rollups are in `data/rollups/`.

| organ | 2020 pop-weighted HI | 2021 pop-weighted HI |
|---|---|---|
| respiratory | 0.350 | 0.339 |
| neurological | 0.020 | 0.022 |
| liver | 0.007 | 0.007 |
| kidney | 0.003 | 0.003 |
| immunological | 0.010 | 0.010 |
| developmental | 0.038 | 0.037 |
| reproductive | 0.048 | 0.047 |
| blocks with respiratory HI > 1 | 17,629 | 5,267 |

## Re-scored series: every year with 2020 toxicity values (added in v1.1)

Each assessment used the toxicity values current at the time, so part of the change between years is a change in toxicity values, not in air. `data/rescored_2020tox/` re-scores each year's tract cancer risk with the 2020 unit risk estimates:

    re-scored = EPA's published tract total + sum over pollutants of (URE_2020 / URE_that_year - 1) x EPA's risk for that pollutant

The unit risk EPA used each year (`validation/ure_by_year.csv`) comes from:
- **1996, 1999, 2002, 2018, 2019:** recovered from EPA's own risk and exposure tables (risk / exposure, per tract; spread under 0.1% for single substances).
- **2005, 2011:** read from the dose-response tables in EPA's documentation.
- **2014, 2017:** taken from EPA's pollutant spreadsheets.

2014 through 2020 turned out to use the same values, so only 1996-2011 change. The big ones:
- **Formaldehyde:** 1999 and 2002 used an industry-funded potency (5.5e-9, about 2,400x lower than the IRIS value EPA has used since 2005).
- **Ethylene oxide:** 57x higher after EPA's 2016 IRIS update.
- **Trichloroethylene and ethylene dibromide:** higher now.
- **Methylene chloride, perchloroethylene and carbon tetrachloride:** lower now.
- **No longer scored for cancer:** about a dozen pollutants, including chloroform, quinoline and 1,1,2,2-tetrachloroethane. They get zero.

Not re-scored, and kept as published: lumped groups and metal compounds (POM/PAH groups, chromium, nickel, PCBs). EPA folds speciation and group-specific values into those numbers, so a single ratio would be wrong. The summary lists them per year.

| year | tracts | pop-weighted cancer risk per million, as published | re-scored with 2020 values | biggest re-scoring changes (per million) |
|---|---|---|---|---|
| 1996 | 60,287 | 55.2 | 63.7 | Ethylene Oxide +14.5, Carbon Tetrachloride -5.8, Ethylene Dibromide +2.3, Chloroform -1.7 |
| 1999 | 66,028 | 41.5 | 84.4 | Ethylene oxide +22.8, Formaldehyde +19.3, Ethylene dibromide +6.9, 1,1,2,2-Tetrachloroethane -2.6 |
| 2002 | 66,029 | 35.6 | 84.9 | ETHYLENE OXIDE +34.7, FORMALDEHYDE +20.0, CARBON TETRACHLORIDE -4.3, TETRACHLOROETHYLENE (PERCHLOROETHYLENE) -1.1 |
| 2005 | 66,029 | 49.8 | 80.2 | ETHYLENE OXIDE +30.5, 1,1,2,2-TETRACHLOROETHANE -0.2, ETHYLBENZENE +0.2, METHYLENE CHLORIDE (DICHLOROMETHANE) -0.1 |
| 2011 | 73,450 | 39.9 | 43.7 | ETHYLENE OXIDE +3.8 |
| 2014 | 73,450 | 31.7 | 31.7 |  |
| 2017 | 73,449 | 28.7 | 28.7 |  |
| 2018 | 73,449 | 25.0 | 25.0 |  |
| 2019 | 73,449 | 25.4 | 25.4 |  |

1996 covers only the 33 pollutants EPA assessed that year, so its total isn't comparable to later years.

This removes only the toxicity-value differences. Emissions inventories, models, pollutant coverage and census geography still differ between assessments, so the re-scored series is closer to comparable but is not a clean trend.

## Layout

```
data/
  epa_published/<year>/   EPA's published tables, long format (see schema)
  blocks/2020/            EPA 2020 block-level cancer risk, per region: _by_poll, _by_src (wide)
  blocks/2021/            our 2021 recompute, same layout + PAHPOM estimate columns
  rollups/                2020 and 2021 tract and county tables: population-weighted means of the blocks
  hazard/2020, hazard/2021 noncancer HQ and organ HI per block (recomputed, see below)
  rescored_2020tox/       every year's tract cancer risk re-scored with 2020 toxicity values
validation/               method check, PAHPOM test, recovered toxicity values, sector-sum checks
ref/                      EPA 2020 pollutant table (unit risk estimates)
```

### Long schema (data/epa_published)

`year, geo_level (nation|state|county|tract), geoid, state, county, population, dimension, name, metric, value, unit, origin, source_file`

- `dimension`: `pollutant`, `source` (sector or source group), `source_category` (2005 sub-sector files), `total`, or `statistic` (1999 county distributions).
- `metric`: `cancer_risk` (per million), `hazard_index_<organ>`, `hazard_quotient`, `ambient_conc` / `exposure_conc` (ug/m3), `emissions` (tons/yr), and for 1999 state files `<metric>_percent_of_total` (each pollutant's share of the tract total, in percent).
- `source_file`: the raw EPA file (and Access table) each row came from.
- 1999 tract files and 1996 files are one Parquet per pollutant and add `pollutant` (and `cas`, `urban_rural` for 1999) columns.

### Block tables (data/blocks)

One row per census block: `State, County, FIPS, Block, Population`, then one column per pollutant (`_by_poll`) or source group (`_by_src`), then `total_cancer_risk_per_million`. `_by_poll` also has `total_rounded_epa_style`, the total rounded to one significant figure the way EPA's published totals are. 2021 adds `PAHPOM` (estimate), `total_excl_pahpom`, `pahpom_share_no_source_ratio`, and `pahpom_ratio_level`. 2020 and 2021 both use 2020 census block IDs, so you can join the two years on `Block`. EPA's 2021 files leave out some blocks that are in its 2020 files, for example 544 in Region 2. Every 2021 block is in 2020.

## What's in each year

| year | geography | what EPA published (and is here) |
|---|---|---|
| 1996 | tract | 33 pollutants: concentration, exposure, risk tables (per-pollutant xls) |
| 1999 | tract, county, state | 188 pollutants per tract: ambient + exposure conc, cancer risk, hazard quotient by sector; state tract totals (cancer, respiratory, neurological); county emissions; county concentration distributions |
| 2002 | tract | cancer risk, respiratory and neurological HI by pollutant and sector |
| 2005 | tract | cancer, respiratory, neurological by pollutant and sector; onroad, nonroad, nonpoint by sub-category |
| 2011, 2014 | tract | cancer risk and hazard indexes by pollutant and source group |
| 2017-2019 | tract | same, AirToxScreen |
| 2020 | block (+ tract/county rollups) | cancer risk by pollutant and source group (EPA gaftp per-pollutant files); noncancer HQ/HI recomputed here |
| 2021 | block (+ rollups) | **recomputed here**: cancer risk by pollutant and source group; noncancer HQ/HI |

## Known quirks in EPA's files

- EPA's published totals for 2017-2020 are rounded to one significant figure. The exact sum is in `total_cancer_risk_per_million`, the rounded one in `total_rounded_epa_style`, or in EPA's own `TOTAL` rows for the tract years.
- 2005 onroad file: in 855 Puerto Rico tracts EPA's total is about 10x gasoline + diesel. Kept as published.
- 2020 `PAHPOM_RISK_R3.csv` has "Exposure Conc" column headers over risk values. The values are risk (about 55x the exposure file), so we read them as risk.
- 2020 Region 9 per-pollutant CSVs drop the leading zero on about 1,000-2,000 California and Arizona block IDs, and which blocks are affected differs between files. IDs here are zero-padded to 15 digits (block) and 5 digits (county FIPS).
- US Virgin Islands blocks have zero population in EPA's 2020 and 2021 block files, so their tract and county rollups here are plain means of the blocks. EPA's own 2020 county file uses population-weighted values and differs by up to 22% for those 3 counties. Every other county matches EPA's county file within EPA's rounding.
- 2021 Region 10 CSVs leave commas in pollutant names unquoted (`1,3-BUTADIENE`). The code re-quotes them.
- 2021 headers have typos (`BENZOAPYRNE`). The code maps them to the 2020 names.
- A few 1999 and 2002 files linked from EPA's pages are gone and aren't in the Wayback Machine either: 2002 tract databases for asbestos, fine mineral fibers, radionuclides and hexachlorocyclohexane, and one 1999 county database. Other dead 1999 links point to files that exist under a different folder, and those are included.

## Changes

- **v1.1 (2026-11-04).** Added: noncancer hazard for 2020 and 2021, the re-scored series, and the per-year toxicity table. Fixed two conversion errors of ours in v1:
  - 1999 cancer risk for benzyl chloride, 2,4-toluene diamine and POM was 1,000,000x too small. Those EPA tables store risk as a probability, not per million.
  - The 2014, 2017, 2018 and 2019 all-organ HI files had shifted columns in 2014 and 2019 and a misleading metric name. They now have one row per organ with `metric = hazard_index`. EPA's 2014, 2018 and 2019 all-organ files carry no state or county names, so join on `geoid`.
- **v1 (2026-11-04).** First release.

## Not included

- The raw EPA files (about 127 GB). They were mirrored for this build, and the Data Rescue Project and Zenodo record 17187663 already archive them.
- Noncancer re-scoring of older years.

## Credit and license

All input data is from the U.S. EPA (public domain). This compilation, the 2021 recompute, and the code are released under CC BY 4.0 (data) and MIT (code). Questions about EPA's underlying data: airtoxics@epa.gov. Thanks to the Data Rescue Project and the Internet Archive for keeping copies of EPA's older assessment pages.
