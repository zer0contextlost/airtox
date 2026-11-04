"""Recover the toxicity values EPA actually used in a year from its own published tables.

usage: python -I infer_tox.py <year> <rawdir> <pollutants2020.xlsx> <out.csv>
For each pollutant: URE = cancer_risk(per million) / 1e6 / exposure_conc, per tract. If EPA's risk is EC x URE,
the ratio is the same in every tract (spread ~0). Same for noncancer: HQ = EC / (RfC_mg * 1000) -> RfC.
Writes one row per pollutant with the inferred values, their spread, and the 2020 table values for comparison.
"""
import glob
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def wide(path):
    d = pd.read_excel(path, engine="calamine", dtype={3: str, 4: str})
    d = d[~d.iloc[:, 4].astype(str).str.endswith("000000")]  # tracts only
    d.index = d.iloc[:, 4].astype(str)
    return d


def ratios(num, den, scale):
    out = {}
    nmap = {norm(c): c for c in num.columns[6:]}
    for c in den.columns[5:]:
        k = norm(c)
        if k not in nmap:
            continue
        a = pd.to_numeric(num[nmap[k]], errors="coerce")
        b = pd.to_numeric(den[c], errors="coerce").reindex(a.index)
        m = (a > 0) & (b > 0)
        if m.sum() < 50:
            continue
        r = (a[m] / b[m]) * scale
        out[k] = (c, float(r.median()), float((r.quantile(0.99) - r.quantile(0.01)) / r.median()), int(m.sum()))
    return out


def main(year, rawdir, poll2020, out_csv):
    d = f"{rawdir}/{year}/epa"
    exp = pd.read_excel(glob.glob(f"{d}/*Exposure_Concentrations.xlsx")[0], engine="calamine", dtype={3: str, 4: str})
    exp = exp[~exp.iloc[:, 4].astype(str).str.endswith("000000")]
    exp.index = exp.iloc[:, 4].astype(str)
    can = wide(glob.glob(f"{d}/*CancerRisk_by_tract_poll.xlsx")[0])
    rows = {}
    for k, (name, v, spread, n) in ratios(can, exp, 1e-6).items():
        rows.setdefault(k, {"pollutant": name})
        rows[k].update(ure_inferred=v, ure_spread=spread, ure_n=n)
    for f in sorted(glob.glob(f"{d}/*HI_by_tract_poll.xlsx")):
        organ = re.search(r"_(\w+?)HI_by", f).group(1).lower()
        hi = wide(f)
        for k, (name, v, spread, n) in ratios(hi, exp, 1.0).items():
            rows.setdefault(k, {"pollutant": name})
            rows[k][f"rfc_{organ}"] = 1.0 / v / 1000.0 if v else np.nan  # HQ/EC = 1/(RfC mg->ug)
            rows[k][f"rfc_{organ}_spread"] = spread
    t = pd.read_excel(poll2020)
    ref = {}
    for _, r in t.iterrows():
        k = norm(r["AirToxScreen WEBSITE POLL NAME (comment)"])
        if k not in ref and (pd.notna(r["URE 1/(ug/m3)"]) or pd.notna(r["RFC (mg/m3)"])):
            ref[k] = (r["URE 1/(ug/m3)"], r["RFC (mg/m3)"])
    for k, row in rows.items():
        row["ure_2020table"], row["rfc_2020table"] = ref.get(k, (np.nan, np.nan))
    res = pd.DataFrame(rows.values())
    res.to_csv(out_csv, index=False)
    c = res.dropna(subset=["ure_inferred"])
    print(f"{year}: {len(c)} carcinogens inferred; max spread {c.ure_spread.max():.2e}")
    diff = c[(c.ure_2020table > 0) & (abs(c.ure_inferred / c.ure_2020table - 1) > 0.01)]
    print("URE differs from 2020 table (>1%):")
    print(diff[["pollutant", "ure_inferred", "ure_2020table", "ure_spread"]].to_string(index=False))
    rf = [c for c in res.columns if c.startswith("rfc_") and not c.endswith(("spread", "table"))]
    print("noncancer organs:", rf, "max spread:",
          max(res[c + "_spread"].max() for c in rf if c + "_spread" in res))


if __name__ == "__main__":
    main(*sys.argv[1:5])
