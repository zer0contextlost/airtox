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
| 2020 | 5,808,484 | 334,725,204 | 30.0 | 1.10 | epa_published |
| 2021 | 5,807,940 | 334,725,204 | 27.8 | 1.07 (estimate) | recomputed_2021 |

EPA cautions that years are not directly comparable: emissions inventories, models and toxicity values change between assessments.

## Layout

```
data/
  epa_published/<year>/   EPA's published tables, long format (see schema)
  blocks/2020/            EPA 2020 block-level cancer risk, per region: _by_poll, _by_src (wide)
  blocks/2021/            our 2021 recompute, same layout + PAHPOM estimate columns
  rollups/                2020 and 2021 tract and county tables: population-weighted means of the blocks
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
| 2020 | block (+ tract/county rollups) | cancer risk by pollutant and source group (EPA gaftp per-pollutant files) |
| 2021 | block (+ rollups) | **recomputed here**: cancer risk by pollutant and source group |

## Known quirks in EPA's files

- EPA's published totals for 2017-2020 are rounded to one significant figure. The exact sum is in `total_cancer_risk_per_million`, the rounded one in `total_rounded_epa_style`, or in EPA's own `TOTAL` rows for the tract years.
- 2005 onroad file: in 855 Puerto Rico tracts EPA's total is about 10x gasoline + diesel. Kept as published.
- 2020 `PAHPOM_RISK_R3.csv` has "Exposure Conc" column headers over risk values. The values are risk (about 55x the exposure file), so we read them as risk.
- 2020 Region 9 per-pollutant CSVs drop the leading zero on about 1,000-2,000 California and Arizona block IDs, and which blocks are affected differs between files. IDs here are zero-padded to 15 digits (block) and 5 digits (county FIPS).
- US Virgin Islands blocks have zero population in EPA's 2020 and 2021 block files, so their tract and county rollups here are plain means of the blocks. EPA's own 2020 county file uses population-weighted values and differs by up to 22% for those 3 counties. Every other county matches EPA's county file within EPA's rounding.
- 2021 Region 10 CSVs leave commas in pollutant names unquoted (`1,3-BUTADIENE`). The code re-quotes them.
- 2021 headers have typos (`BENZOAPYRNE`). The code maps them to the 2020 names.
- A few 1999 and 2002 files linked from EPA's pages are gone and aren't in the Wayback Machine either: 2002 tract databases for asbestos, fine mineral fibers, radionuclides and hexachlorocyclohexane, and one 1999 county database. Other dead 1999 links point to files that exist under a different folder, and those are included.

## Not included

- The raw EPA files (about 127 GB). They were mirrored for this build, and the Data Rescue Project and Zenodo record 17187663 already archive them.
- 2021 noncancer hazard indexes. The RfCs are in the same table, but the method hasn't been checked against 2020 yet.
- 1996 and 1999 values rescored with current toxicity values.

## Credit and license

All input data is from the U.S. EPA (public domain). This compilation, the 2021 recompute, and the code are released under CC BY 4.0 (data) and MIT (code). Questions about EPA's underlying data: airtoxics@epa.gov. Thanks to the Data Rescue Project and the Internet Archive for keeping copies of EPA's older assessment pages.
