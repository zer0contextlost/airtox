"""EPA's published 2020 AirToxScreen block-level cancer risk, by pollutant and by source group, as parquet.

usage: python -I convert_2020_blocks.py <region 1-10> <out_dir>
Reads EPA's per-pollutant RISK CSVs (gaftp Pollutant Summaries/Region<n>/*_RISK_R<n>.zip), same layout as
the 2021 recompute so the two years line up column for column. Every value is EPA's own; the by-source table
sums EPA's per-pollutant source columns. EPA's TOTAL in its region xlsx is rounded to 1 significant figure;
total_cancer_risk_per_million here is the exact sum and total_rounded_epa_style the rounded one.
"""
import glob
import json
import os
import resource
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_csv import DIRS, IDS, align, read_zip, sig1  # noqa: E402


def main(region, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    zips = sorted(glob.glob(f"{DIRS['2020']}/Region{region}/*_RISK_R{region}.zip"))
    ids, by_poll, src_names, src_acc = None, {}, [], None
    for zp in zips:
        df, poll = read_zip(zp, "Cancer Risk (per million)")
        if ids is None:
            ids = df[IDS].copy()
            blocks = ids["Block"].to_numpy()
        else:
            df = align(df, blocks)
        srcs = [c for c in df.columns if c not in IDS and c != "Total"]
        by_poll[poll] = df["Total"].to_numpy(float)
        if src_acc is None:
            src_names, src_acc = list(srcs), np.zeros((len(blocks), len(srcs)))
        for c in srcs:
            if c not in src_names:
                src_names.append(c)
                src_acc = np.hstack([src_acc, np.zeros((len(blocks), 1))])
            src_acc[:, src_names.index(c)] += df[c].to_numpy(float)
        del df
        print(f"R{region} 2020 {poll}", flush=True)
    total = np.zeros(len(blocks))
    for v in by_poll.values():
        total += v
    idcols = {c: pa.array(ids[c].to_numpy()) for c in IDS}
    cols = dict(idcols)
    cols.update({p: pa.array(v) for p, v in by_poll.items()})
    cols["total_cancer_risk_per_million"] = pa.array(total)
    cols["total_rounded_epa_style"] = pa.array(np.array([sig1(x) for x in total]))
    pq.write_table(pa.table(cols), f"{out_dir}/2020_R{region}_by_poll.parquet")
    cols, src_sum = dict(idcols), np.zeros(len(blocks))
    for i, c in enumerate(src_names):
        src_sum += src_acc[:, i]
        cols[c] = pa.array(src_acc[:, i])
    cols["total_cancer_risk_per_million"] = pa.array(total)
    pq.write_table(pa.table(cols), f"{out_dir}/2020_R{region}_by_src.parquet")
    pop = ids["Population"].to_numpy(float)
    summ = {"year": "2020", "region": region, "blocks": int(len(blocks)), "population": float(pop.sum()),
            "pollutants": len(by_poll), "source_groups": len(src_names), "origin": "epa_published",
            "popwtd_total": float(np.average(total, weights=pop)),
            "src_vs_poll_max_abs_gap": float(np.abs(src_sum - total).max()),
            "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}
    json.dump(summ, open(f"{out_dir}/2020_R{region}_summary.json", "w"), indent=1)
    print(json.dumps(summ), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
