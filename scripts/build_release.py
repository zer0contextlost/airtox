"""Assemble the public release tree from /srv/airtox/derived.

usage: python -I build_release.py <derived_dir> <release_dir>
- data/epa_published/<year>/...    hard links to the harmonized EPA tables (no copies)
- data/blocks/{2020,2021}/...       block-level by pollutant / by source group per region (2020 EPA, 2021 recomputed)
- data/rollups/{2020,2021}_{tract,county}_{by_poll,by_src}.parquet
                                    population-weighted means of the block values (our aggregation, labeled)
- validation/                       checks and summaries
Block tables are wide (one column per pollutant or source group); everything else uses the long schema.
"""
import glob
import json
import os
import shutil
import sys

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

IDS = ["State", "County", "FIPS", "Block", "Population"]


def link(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    os.link(src, dst)


def rollup(files, level):
    """Population-weighted mean of every numeric column, per tract (Block[:11]) or county (FIPS)."""
    parts = []
    for f in files:
        t = pq.read_table(f)
        key = pc.utf8_slice_codeunits(t["Block"], 0, 11) if level == "tract" else t["FIPS"]
        pop = t["Population"].to_numpy().astype(float)
        keys, inv = np.unique(key.to_numpy(zero_copy_only=False), return_inverse=True)
        wsum = np.bincount(inv, weights=pop)
        n = np.bincount(inv)
        st = t["State"].to_numpy(zero_copy_only=False)
        first = np.zeros(len(keys), dtype=int)
        first[inv[::-1]] = np.arange(len(inv))[::-1]
        cols = {"geoid": keys, "state": st[first], "population": wsum, "blocks": n}
        if level == "county":
            cols["county"] = t["County"].to_numpy(zero_copy_only=False)[first]
        for c in t.column_names:
            if c in IDS or c in ("pahpom_ratio_level",):
                continue
            v = t[c].to_numpy().astype(float)
            ws = np.bincount(inv, weights=v * pop)
            us = np.bincount(inv, weights=v)
            # zero-population areas: plain mean of their blocks
            cols[c] = np.where(wsum > 0, ws / np.where(wsum > 0, wsum, 1), us / n)
        parts.append(pa.table(cols))
        del t
    return pa.concat_tables(parts, promote_options="default")


def main(derived, rel):
    os.makedirs(rel, exist_ok=True)
    pub = os.path.join(derived, "epa_published")
    for f in glob.glob(f"{pub}/**/*.parquet", recursive=True):
        if "/2020/" in f:
            continue  # 2020 block tables go under data/blocks
        link(f, os.path.join(rel, "data/epa_published", os.path.relpath(f, pub)))
    for f in glob.glob(f"{pub}/**/*.csv", recursive=True):
        link(f, os.path.join(rel, "validation/1999", os.path.basename(f)))
    srcdir = {"2020": f"{pub}/2020", "2021": f"{derived}/recomputed_2021"}
    for y, d in srcdir.items():
        for f in glob.glob(f"{d}/{y}_R*.parquet") + glob.glob(f"{d}/{y}_R*_summary.json"):
            link(f, os.path.join(rel, "data/blocks", y, os.path.basename(f)))
        for kind in ("by_poll", "by_src"):
            files = sorted(glob.glob(f"{d}/{y}_R*_{kind}.parquet"))
            for level in ("tract", "county"):
                out = os.path.join(rel, f"data/rollups/{y}_{level}_{kind}.parquet")
                os.makedirs(os.path.dirname(out), exist_ok=True)
                t = rollup(files, level)
                pq.write_table(t, out)
                print(f"rollup {y} {level} {kind}: {t.num_rows} areas", flush=True)
    # noncancer hazard (2020 + 2021 recomputed), blocks + rollups
    for y in ("2020", "2021"):
        hd = f"{derived}/hazard_{y}"
        for f in glob.glob(f"{hd}/{y}_R*"):
            link(f, os.path.join(rel, "data/hazard", y, os.path.basename(f)))
        for kind in ("hi", "resp_hi_by_src"):
            files = sorted(glob.glob(f"{hd}/{y}_R*_{kind}.parquet"))
            if not files:
                continue
            for level in ("tract", "county"):
                out = os.path.join(rel, f"data/rollups/{y}_{level}_{kind}.parquet")
                t = rollup(files, level)
                pq.write_table(t, out)
                print(f"rollup {y} {level} {kind}: {t.num_rows} areas", flush=True)
    # rescored series (2020 toxicity values applied to every year)
    for f in glob.glob(f"{derived}/rescored/*"):
        link(f, os.path.join(rel, "data/rescored_2020tox", os.path.basename(f)))
    link(f"{derived}/validate_hi_2019.json", os.path.join(rel, "validation", "noncancer_method_check_2019.json"))
    for f in glob.glob(f"{derived}/tox/*.csv") + [f"{derived}/pahpom_transfer_2019_2020_county.csv"]:
        link(f, os.path.join(rel, "validation", os.path.basename(f)))
    test = f"{derived}/risk_csv_test/2020_R1_summary.json"
    if os.path.exists(test):
        shutil.copy(test, os.path.join(rel, "validation", "recompute_method_check_2020_R1.json"))
    # national population-weighted totals from the block summaries
    rows = []
    for y, d in srcdir.items():
        S = [json.load(open(f)) for f in glob.glob(f"{d}/{y}_R*_summary.json")]
        P = sum(s["population"] for s in S)
        rows.append({"year": int(y), "blocks": sum(s["blocks"] for s in S), "population": P,
                     "popwtd_total_cancer_risk_per_million": sum(s["popwtd_total"] * s["population"] for s in S) / P,
                     "popwtd_pahpom": (sum(s["popwtd_pahpom"] * s["population"] for s in S) / P) if "popwtd_pahpom" in S[0] else None,
                     "origin": "recomputed_2021" if y == "2021" else "epa_published"})
    json.dump(rows, open(os.path.join(rel, "validation", "national_block_totals_2020_2021.json"), "w"), indent=1)
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
