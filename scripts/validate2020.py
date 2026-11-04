"""Reproduce EPA's 2020 AirToxScreen block-level cancer risk from its exposure concentrations.

usage: python -I validate2020.py <exposure.xlsx> <cancer_by_block_poll.xlsx> <pollutants.xlsx>
Risk = EC (ug/m3) x URE (1/(ug/m3)), URE taken from EPA's 2020 pollutant table
(childhood-adjusted UREs already folded in). Compares per pollutant and per-block total.
"""
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ID_COLS = 6  # State, Region, County, FIPS, Block, Population


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def load(path):
    df = pd.read_excel(path, engine="calamine", dtype={3: str, 4: str})
    df.columns = [str(c).strip() for c in df.columns]
    return df


def main(exp_path, can_path, poll_path):
    exp, can = load(exp_path), load(can_path)
    print("exposure", exp.shape, "cancer", can.shape)
    print("cancer header sample:", list(can.columns[:ID_COLS + 4]), "...", list(can.columns[-3:]))

    tox = pd.read_excel(poll_path)
    name_col = "AirToxScreen WEBSITE POLL NAME (comment)"
    ure = {}
    for _, r in tox.iterrows():
        u = r["URE 1/(ug/m3)"]
        if pd.notna(u) and u > 0:
            ure.setdefault(norm(r[name_col]), float(u))

    blk_e, blk_c = exp.columns[4], can.columns[4]
    m = exp.merge(can, left_on=blk_e, right_on=blk_c, suffixes=("_e", "_c"))
    print("blocks matched:", len(m), "of", len(exp), "/", len(can))

    can_polls = {norm(c): c for c in can.columns[ID_COLS:]}
    exp_polls = {norm(c): c for c in exp.columns[ID_COLS:]}
    rows, ours_total = [], np.zeros(len(m))
    for key, ccol in can_polls.items():
        if key in ("TOTAL", "TOTALCANCERRISK", "TOTALRISK"):
            continue
        ecol = exp_polls.get(key)
        epa = m[ccol + "_c" if ccol + "_c" in m else ccol].to_numpy(float)
        if ecol is None or key not in ure:
            rows.append((ccol, "NO_EC" if ecol is None else "NO_URE", np.nan, np.nan, float(np.nansum(epa))))
            continue
        ec = m[ecol + "_e" if ecol + "_e" in m else ecol].to_numpy(float)
        ours = ec * ure[key]
        ours_total += np.nan_to_num(ours)
        mask = (epa > 0) & (ours > 0)
        ratio = epa[mask] / ours[mask]
        rows.append((ccol, "ok", float(np.median(ratio)) if mask.any() else np.nan,
                     float(np.max(np.abs(ratio / np.median(ratio) - 1))) if mask.any() else np.nan,
                     float(np.nansum(epa))))
    res = pd.DataFrame(rows, columns=["pollutant", "status", "median_EPA_over_ours", "max_rel_dev", "EPA_sum"])
    pd.set_option("display.width", 200)
    print(res.sort_values("EPA_sum", ascending=False).head(12).to_string(index=False))
    print(res[res.status != "ok"].to_string(index=False))
    print("cancer cols not in exposure/URE:", [c for c in can.columns[ID_COLS:] if norm(c) not in exp_polls or norm(c) not in ure])
    print("status counts:", res.status.value_counts().to_dict())

    scale = np.nanmedian(res.loc[res.status == "ok", "median_EPA_over_ours"])
    print("inferred unit scale (EPA/ours):", scale)
    tot_col = next((c for c in can.columns if norm(c).startswith("TOTAL")), None)
    if tot_col:
        epa_tot = m[tot_col].to_numpy(float)
        ok = epa_tot > 0
        rel = (ours_total[ok] * scale) / epa_tot[ok] - 1
        print(f"per-block TOTAL: n={ok.sum()} median rel err={np.median(rel):.2e} "
              f"p99 |err|={np.percentile(np.abs(rel), 99):.2e} max |err|={np.max(np.abs(rel)):.2e}")
        pop = m[[c for c in m.columns if c.startswith("Population")][0]].to_numpy(float)
        print("pop-weighted mean total risk  EPA:", np.average(epa_tot, weights=pop),
              " ours:", np.average(ours_total * scale, weights=pop))


if __name__ == "__main__":
    main(*sys.argv[1:4])
