"""Convert NATA 1996 per-pollutant tract workbooks (<code>-tract.zip) to the long schema.

usage: python -I harmonize_1996.py <rawdir> <outdir>
Each workbook has one data sheet: State/County names, 1990 tract number, urban/rural, 1990 population, then
exposure concentration (ug/m3), lifetime cancer risk (probability) and chronic hazard quotient, each split by
source (Major, Area and Other, Onroad Mobile, Nonroad Mobile, Estimated Background) plus Total.
County FIPS comes from matching state+county names against the 2002 NATA tables; unmatched counties keep
geoid = None and are listed in the log. Geography is 1990 census tracts.
"""
import os
import re
import sys
import tempfile
import zipfile

import pandas as pd
import pyarrow.dataset as ds
import xlrd

STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
          "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
          "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
          "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
          "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
          "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
          "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
          "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
          "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR", "Virgin Islands": "VI"}
SOURCES = ["Major", "Area and Other", "Onroad Mobile", "Nonroad Mobile", "Estimated Background", "Total"]
BLOCKS = [("exposure_conc", "ug_m3", 1.0, 5), ("cancer_risk", "per_million", 1e6, 11),
          ("hazard_quotient", "hazard_quotient", 1.0, 17)]


def ckey(name):
    s = str(name).lower().replace("saint ", "st ").replace("st. ", "st ")
    s = re.sub(r"\b(county|parish|borough|census area|city and borough|municipality|municipio|island)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def county_lookup(outdir):
    t = ds.dataset(os.path.join(outdir, "2002", "cancer_risk_tract.parquet")).to_table(
        columns=["state", "county", "geoid"], filter=(ds.field("geo_level") == "tract")).to_pandas()
    t["fips"] = t.geoid.str[:5]
    t = t.drop_duplicates(["state", "county", "fips"])
    lut = {}
    for st, cn, f in zip(t.state, t.county, t.fips):
        lut.setdefault((st, ckey(cn)), f)
    return lut


MANUAL = {("FL", "dade"): "12086",  # Dade County became Miami-Dade (12025 -> 12086) in 1997
          ("PR", "coama"): "72043"}  # EPA typo for Coamo


def find_fips(lut, st, name):
    """Exact name match, else a unique prefix match (EPA cut 1996 county names to 20 characters)."""
    k = ckey(name)
    if (st, k) in lut:
        return lut[(st, k)]
    if (st, k) in MANUAL:
        return MANUAL[(st, k)]
    raw = re.sub(r"[^a-z]", "", str(name).lower())
    hits = [(len(k2), f) for (s2, k2), f in lut.items() if s2 == st and k2 and (k2.startswith(k) or raw.startswith(k2))]
    if not hits:
        return None
    hits.sort(reverse=True)  # "Lake of the Woods Co" also prefix-matches "Lake": prefer the longest name
    if len(hits) > 1 and hits[0][0] == hits[1][0] and hits[0][1] != hits[1][1]:
        return None
    return hits[0][1]


def convert(zpath, lut, out_path):
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zpath) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".xls")][0]
            z.extract(name, tmp)
        book = xlrd.open_workbook(os.path.join(tmp, name), on_demand=True)
        sh = [book.sheet_by_name(n) for n in book.sheet_names() if n.lower() != "population"][0]
        title = str(sh.cell_value(0, 0))
        m = re.search(r"for (.+?)(?:\s*\(CAS\s*#?\s*([\w-]+)\))?\s*$", title.strip())
        pollutant, cas = (m.group(1).strip(), m.group(2) or "") if m else (title, "")
        rows = [sh.row_values(i) for i in range(3, sh.nrows)]
    df = pd.DataFrame(rows)
    df = df[df[0].astype(str).str.strip() != ""]
    upper = {k.upper(): v for k, v in STATES.items()}
    st_abbr = df[0].map(lambda s: upper.get(re.sub(r"\s+", " ", str(s)).strip().upper()))
    cf = [find_fips(lut, a, c) for a, c in zip(st_abbr, df[1])]
    tract = df[2].map(lambda v: f"{int(float(v)):06d}" if str(v).strip() not in ("", "nan") else None)
    base = pd.DataFrame({"year": 1996, "geo_level": "tract",
                         "geoid": [f + t if f and t else None for f, t in zip(cf, tract)],
                         "state": st_abbr.values, "county": df[1].astype(str).str.strip().values,
                         "population": pd.to_numeric(df[4], errors="coerce").values,
                         "tract_1990": tract.values, "urban_rural": df[3].astype(str).values})
    parts = []
    for metric, unit, scale, start in BLOCKS:
        for j, src in enumerate(SOURCES):
            if start + j >= df.shape[1]:
                continue
            p = base.copy()
            p["dimension"] = "total" if src == "Total" else "source"
            p["name"] = f"{pollutant}|{src}" if src != "Total" else pollutant
            p["pollutant"], p["cas"] = pollutant, cas
            p["metric"], p["unit"], p["origin"] = metric, unit, "epa_published"
            p["value"] = pd.to_numeric(df[start + j], errors="coerce").values * scale
            p["source_file"] = os.path.relpath(zpath, os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(zpath))))))
            parts.append(p)
    out = pd.concat(parts, ignore_index=True)
    out = out[out.value.notna()]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_parquet(out_path, index=False)
    unmatched = sorted({(str(a), str(c)) for a, c, f in zip(st_abbr, df[1], cf) if not f})
    return pollutant, len(df), len(out), unmatched


def main(rawdir, outdir):
    zdir = os.path.join(rawdir, "1996/archive/airtoxics/nata/web/zip")
    lut = county_lookup(outdir)
    files = sorted(f for f in os.listdir(zdir) if re.match(r"\d+-tract\.zip$", f))
    print(f"{len(files)} pollutant files, {len(lut)} counties in lookup", flush=True)
    all_unmatched = set()
    for i, f in enumerate(files, 1):
        out = os.path.join(outdir, "1996", f.replace(".zip", ".parquet"))
        try:
            pol, n, nl, um = convert(os.path.join(zdir, f), lut, out)
            all_unmatched |= set(um)
            print(f"[{i}/{len(files)}] ok {f} {pol} tracts={n} long={nl} unmatched_counties={len(um)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(files)}] FAILED {f}: {e!r}", flush=True)
    print("UNMATCHED", sorted(all_unmatched), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
