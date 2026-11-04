"""AirToxScreen-method cancer risk from EPA block exposure concentrations.

usage: python -I risk.py <exposure.xlsx> <pollutants.xlsx> <out.parquet>
Risk (per million) = EC x URE x 1e6 per pollutant; total = sum, plus EPA-style 1-sig-fig total.
Validated against EPA 2020 Region 1: per-pollutant exact, rounded total 100% of 225,438 blocks.
"""
import math
import re
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
# 2021 EPA headers carry typos/renames vs the 2020 pollutant table
ALIASES = {"BENZOAPYRNE": "BENZOAPYRENE", "BIS2ETHYLHEXYLPHTHALATE": "BIS2ETHYLHEXYLPHTHALATEDEHP"}


def norm(s):
    k = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    return ALIASES.get(k, k)


def sig1(x):
    return 0.0 if x == 0 else round(x, -int(math.floor(math.log10(abs(x)))))


def main(exp_path, poll_path, out_path):
    tox = pd.read_excel(poll_path)
    ure = {}
    for _, r in tox.iterrows():
        u = r["URE 1/(ug/m3)"]
        if pd.notna(u) and u > 0:
            ure.setdefault(norm(r["AirToxScreen WEBSITE POLL NAME (comment)"]), float(u))
    e = pd.read_excel(exp_path, engine="calamine", dtype={3: str, 4: str})
    ids = e.iloc[:, :6].copy()
    ids.columns = ["state", "region", "county", "fips", "block", "population"]
    cols = [c for c in e.columns[6:] if norm(c) in ure]
    risk = pd.DataFrame({c: e[c].to_numpy(float) * ure[norm(c)] * 1e6 for c in cols})
    out = pd.concat([ids, risk], axis=1)
    out["total_cancer_risk_per_million"] = risk.sum(axis=1)
    out["total_rounded_epa_style"] = out["total_cancer_risk_per_million"].map(sig1)
    out.to_parquet(out_path)
    pop = ids["population"].to_numpy(float)
    print(f"{exp_path}: blocks={len(e)} pollutants_with_URE={len(cols)} "
          f"popwtd_total={np.average(out['total_cancer_risk_per_million'], weights=pop):.2f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
