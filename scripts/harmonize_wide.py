"""Convert EPA's wide tract tables (NATA 2011/2014, AirToxScreen 2017-2019) into one long schema.

usage: python -I harmonize_wide.py <rawdir> <outdir>
For every *_by_tract_poll / *_by_tract_src* / allhi file found under <rawdir>/<year>/epa/, writes
<outdir>/<year>/<stem>.parquet with columns:
  year, geo_level (nation|state|county|tract), geoid, state, county, population,
  dimension (pollutant|source|total), name, metric, value, unit, origin, source_file
Values are copied as published (origin = epa_published). Nothing is rescaled.
"""
import os
import re
import sys
import time

import pandas as pd

YEARS = ["2011", "2014", "2017", "2018", "2019"]
ORGANS = {"resp": "respiratory", "neur": "neurological", "immu": "immunological", "kidn": "kidney",
          "kidney": "kidney", "liver": "liver", "all": "all_organs_max"}


def classify(path):
    """Return (metric, unit, dimension) from the file name, or None to skip."""
    n = os.path.basename(path).lower()
    if not n.endswith(".xlsx") or "tract" not in n and "allhi" not in n:
        return None
    if "cancer" in n:
        metric, unit = "cancer_risk", "per_million"
    else:
        m = re.search(r"(resp|neur|immu|kidney|kidn|liver|all)(?:hi|_by)", n)
        if not m:
            return None
        metric, unit = f"hazard_index_{ORGANS[m.group(1)]}", "hazard_index"
    if "srcgrp" in n or "source" in n:
        dim = "source"
    elif "poll" in n:
        dim = "pollutant"
    else:
        dim = "mixed"  # allhi: one column per organ, handled as names
    return metric, unit, dim


def geo(row_state, row_county, fips, tract):
    t = str(tract).strip().zfill(11)
    c = str(row_county).strip().lower()
    if t == "00000000000" or c.startswith("entire u"):
        return "nation", "00"
    if c.startswith("entire state"):
        return "state", t[:2]
    if t.endswith("000000"):
        return "county", t[:5]
    return "tract", t


def clean_name(col):
    s = re.sub(r"\s+", " ", str(col)).strip()
    s = re.sub(r"\s*\((per|in a) million\)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*cancer risk$", "", s, flags=re.I)
    s = re.sub(r"\s*(hazard index|hi)$", "", s, flags=re.I)
    return s.strip()


def convert(path, year, out_path):
    metric, unit, dim = classify(path)
    df = pd.read_excel(path, engine="calamine", dtype=str)
    ids = df.iloc[:, :6]
    ids.columns = ["state", "region", "county", "fips", "tract", "population"]
    g = [geo(*r) for r in zip(ids.state, ids.county, ids.fips, ids.tract)]
    base = pd.DataFrame({
        "year": int(year),
        "geo_level": [x[0] for x in g],
        "geoid": [x[1] for x in g],
        "state": ids.state.str.strip(),
        "county": ids.county.str.strip(),
        "population": pd.to_numeric(ids.population, errors="coerce"),
    })
    parts = []
    for col in df.columns[6:]:
        name = clean_name(col)
        is_total = name.lower().startswith("total")
        d = "total" if is_total else ("organ" if dim == "mixed" else dim)
        # 2011 *_by_tract_poll files carry source-group columns first; flag them as source
        if dim == "pollutant" and re.search(r"(risk|hazard)", str(col), re.I) and not is_total:
            d = "source"
        part = base.copy()
        part["dimension"] = d
        part["name"] = "TOTAL" if is_total else name
        part["metric"] = metric
        part["value"] = pd.to_numeric(df[col], errors="coerce")
        part["unit"] = unit
        part["origin"] = "epa_published"
        part["source_file"] = os.path.relpath(path, os.path.dirname(os.path.dirname(os.path.dirname(path))))
        parts.append(part)
    out = pd.concat(parts, ignore_index=True)
    out = out[out.value.notna()]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_parquet(out_path, index=False)
    return len(df), len(out), out.name.nunique()


def main(rawdir, outdir):
    jobs = []
    for y in YEARS:
        d = os.path.join(rawdir, y, "epa")
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if classify(p):
                jobs.append((y, p))
    print(f"{len(jobs)} files", flush=True)
    for i, (y, p) in enumerate(jobs, 1):
        stem = re.sub(r"[^a-z0-9]+", "_", os.path.splitext(os.path.basename(p))[0].lower()).strip("_")
        out_path = os.path.join(outdir, y, stem + ".parquet")
        if os.path.exists(out_path):
            print(f"[{i}/{len(jobs)}] skip {out_path}", flush=True)
            continue
        t = time.time()
        try:
            rows, longrows, names = convert(p, y, out_path)
            print(f"[{i}/{len(jobs)}] ok {y} {os.path.basename(p)} rows={rows} long={longrows} names={names} "
                  f"{time.time() - t:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(jobs)}] FAILED {p}: {e!r}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
