"""Block-level noncancer hazard: HQ per pollutant, hazard index per target organ, respiratory HI by source group.

usage: python -I hi_csv.py <year 2020|2021> <region 1-10> <out_dir>
HQ = exposure conc (ug/m3) / (RfC (mg/m3) x 1000); organ HI = sum of HQ over pollutants EPA flags for that
organ, RfCs and flags from EPA's 2020 pollutant table. Checked against EPA's 2019 tract tables
(scripts/validate_hi.py): same pollutant-organ sets, HQ exact, every organ HI within EPA's rounding.
EPA did not publish block-level hazard for 2020 or any hazard for 2021, so both years are origin "recomputed".
Writes <year>_R<n>_hi.parquet, _hq_by_poll.parquet, _resp_hi_by_src.parquet and a JSON summary.
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
from risk_csv import DIRS, IDS, align, norm, read_zip  # noqa: E402
from validate_hi import ORGANS, load_tox  # noqa: E402


def main(year, region, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tox = load_tox()
    ids, hq, organ_hi, src_names, resp_src = None, {}, {}, [], None
    for zp in sorted(glob.glob(f"{DIRS[year]}/Region{region}/*_EXPCONC_R{region}.zip")):
        df, poll = read_zip(zp, "Exposure Conc")
        key = norm(poll)
        if key not in tox:
            del df
            continue
        if ids is None:
            ids = df[IDS].copy()
            blocks = ids["Block"].to_numpy()
            organ_hi = {o: np.zeros(len(blocks)) for o in ORGANS}
        else:
            df = align(df, blocks)
        rfc, organs = tox[key]
        f = 1.0 / (rfc * 1000.0)
        v = df["Total"].to_numpy(float) * f
        hq[poll] = v
        for o in organs:
            organ_hi[o] += v
        if "resp" in organs:
            srcs = [c for c in df.columns if c not in IDS and c != "Total"]
            if resp_src is None:
                src_names, resp_src = list(srcs), np.zeros((len(blocks), len(srcs)))
            for c in srcs:
                if c not in src_names:
                    src_names.append(c)
                    resp_src = np.hstack([resp_src, np.zeros((len(blocks), 1))])
                resp_src[:, src_names.index(c)] += df[c].to_numpy(float) * f
        del df
        print(f"R{region} {year} {poll}", flush=True)

    idcols = {c: pa.array(ids[c].to_numpy()) for c in IDS}
    cols = dict(idcols)
    cols.update({f"hi_{o}": pa.array(organ_hi[o]) for o in ORGANS})
    pq.write_table(pa.table(cols), f"{out_dir}/{year}_R{region}_hi.parquet")
    cols = dict(idcols)
    cols.update({p: pa.array(v) for p, v in hq.items()})
    pq.write_table(pa.table(cols), f"{out_dir}/{year}_R{region}_hq_by_poll.parquet")
    del cols
    cols, s = dict(idcols), np.zeros(len(blocks))
    for i, c in enumerate(src_names):
        cols[c] = pa.array(resp_src[:, i])
        s += resp_src[:, i]
    cols["hi_resp"] = pa.array(organ_hi["resp"])
    pq.write_table(pa.table(cols), f"{out_dir}/{year}_R{region}_resp_hi_by_src.parquet")

    pop = ids["Population"].to_numpy(float)
    summ = {"year": year, "region": region, "blocks": int(len(blocks)), "population": float(pop.sum()),
            "pollutants_with_rfc": len(hq), "origin": f"recomputed_{year}",
            "popwtd_hi": {o: float(np.average(organ_hi[o], weights=pop)) for o in ORGANS},
            "max_hi": {o: float(organ_hi[o].max()) for o in ORGANS},
            "blocks_resp_hi_over_1": int((organ_hi["resp"] > 1).sum()),
            "resp_src_vs_total_max_abs_gap": float(np.abs(s - organ_hi["resp"]).max()),
            "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}
    json.dump(summ, open(f"{out_dir}/{year}_R{region}_hi_summary.json", "w"), indent=1)
    print(json.dumps({k: summ[k] for k in ("year", "region", "blocks", "pollutants_with_rfc", "blocks_resp_hi_over_1",
                                            "resp_src_vs_total_max_abs_gap", "maxrss_mb")}
                     | {"popwtd_resp": round(summ["popwtd_hi"]["resp"], 4)}), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
