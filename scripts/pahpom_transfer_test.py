"""How well does one year's PAHPOM risk/EC ratio predict the next year's PAHPOM risk? (2019 -> 2020, county level)

usage: python -I pahpom_transfer_test.py <out.csv>
2021 PAHPOM is estimated from 2020's per-block ratio. No later year has published risk to check that against,
so this tests the same kind of carry-forward one year earlier, at the finest geography both years share
(counties; 2019 uses 2010 tracts, 2020 uses 2020 blocks). 2020 county values are population-weighted means of
EPA's block values. Prints error of the carried-forward ratio vs EPA's actual 2020 PAHPOM risk, plus the
naive alternative (one national ratio) for scale.
"""
import glob
import io
import os
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow.csv as pacsv

RAW = os.environ.get("AIRTOX_RAW", "/srv/airtox/raw")  # EPA raw mirror, layout as written by download.py


def county_2019(path):
    d = pd.read_excel(path, engine="calamine", dtype={3: str, 4: str})
    fips, tract = d.iloc[:, 3].astype(str), d.iloc[:, 4].astype(str)
    m = (fips.str.len() == 5) & (tract == fips + "000000")
    return pd.Series(pd.to_numeric(d.loc[m, "PAHPOM"], errors="coerce").to_numpy(), index=fips[m].to_numpy())


def total_col(path):
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as f:
        hdr = f.readline().decode().strip().split(",")
    tot = [c for c in hdr if c.lower().startswith("total")][0]
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as f:
        t = pacsv.read_csv(io.BufferedReader(f, 1 << 22),
                           convert_options=pacsv.ConvertOptions(include_columns=["FIPS", "Block", "Population", tot],
                                                                column_types={"FIPS": "string", "Block": "string"}))
    return t.to_pandas().rename(columns={tot: "v"})


def main(out_csv):
    d19 = f"{RAW}/2019/epa"
    ec19 = county_2019(f"{d19}/2019_Toxics_Exposure_Concentrations.xlsx")
    rk19 = county_2019(f"{d19}/2019_National_CancerRisk_by_tract_poll.xlsx")
    ratio19 = (rk19 / ec19).replace([np.inf, -np.inf], np.nan)
    parts = []
    for rd in sorted(glob.glob(f"{RAW}/2020/gaftp/Pollutant Summaries/Region*")):
        r = rd.rsplit("Region", 1)[1]
        e = total_col(f"{rd}/PAHPOM_EXPCONC_R{r}.zip")
        k = total_col(f"{rd}/PAHPOM_RISK_R{r}.zip").set_index("Block")["v"].reindex(e["Block"]).to_numpy()
        e["risk"] = k
        e["pe"], e["pr"] = e["v"] * e["Population"], e["risk"] * e["Population"]
        parts.append(e.groupby("FIPS")[["Population", "pe", "pr"]].sum())
        print("2020 region", r, len(e), flush=True)
    c20 = pd.concat(parts).groupby(level=0).sum()
    c20 = c20[c20.Population > 0]
    c20["ec20"], c20["risk20"] = c20.pe / c20.Population, c20.pr / c20.Population
    df = c20.join(ratio19.rename("ratio19"), how="inner").join(ec19.rename("ec19"))
    df["ratio20"] = df.risk20 / df.ec20
    df["pred"] = df.ec20 * df.ratio19
    nat = np.average(df.ratio19.dropna(), weights=df.Population[df.ratio19.notna()])
    df["pred_national"] = df.ec20 * nat
    df = df.dropna(subset=["pred", "risk20"])
    df = df[df.risk20 > 0]
    df.to_csv(out_csv)
    rel = df.pred / df.risk20 - 1
    reln = df.pred_national / df.risk20 - 1
    w = df.Population
    print(f"counties={len(df)}  ratio20/ratio19 median={np.median(df.ratio20 / df.ratio19):.3f}")
    for name, e in (("carry-forward county ratio", rel), ("one national ratio", reln)):
        a = e.abs()
        print(f"{name}: median |err|={a.median():.1%} p90={a.quantile(.9):.1%} max={a.max():.1%} "
              f"pop-wtd |err|={np.average(a, weights=w):.1%}")
    print("pop-weighted national PAHPOM risk 2020: EPA", round(np.average(df.risk20, weights=w), 4),
          " carried-forward", round(np.average(df.pred, weights=w), 4))


if __name__ == "__main__":
    main(*sys.argv[1:2])
