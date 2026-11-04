"""Convert NATA 2005 (v4) tract databases to the long schema used by harmonize_wide.py / harmonize_2002.py.

usage: python -I harmonize_2005.py <rawdir> <outdir>
Main files: national tract mdbs with total, source sector and per-pollutant columns for cancer,
respiratory and neurological. Extra files break mobile/nonpoint risk down by emission category
(dimension = "source_category"). Cancer risk is stored by EPA as a probability; converted to per million.
County xls files are EPA's own county roll-ups of the same tract data and are not converted.
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import zipfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# zip -> (metric or None = per-column metric, kind)
FILES = {
    "us_nata05_v4_srcrisk_bytract.zip": ("cancer_risk", "main"),
    "us_nata05_v4_srcresp_bytract.zip": ("hazard_index_respiratory", "main"),
    "us_nata05_v4_srcneur_bytract.zip": ("hazard_index_neurological", "main"),
    "nata05_v4_onroad_mobile_risks_bytract.zip": (None, "category"),
    "nata05_v4_nonroad_mobile_risks_bytract.zip": (None, "category"),
    "nata05_v4_nonpoint_risks_bytract.zip": (None, "category"),
}
SECTORS = ("point", "nonpoint", "onroad", "nonroad", "background", "secondary")
METRIC_WORDS = (("cancer", "cancer_risk"), ("resp", "hazard_index_respiratory"), ("neur", "hazard_index_neurological"),
                ("ambient conc", "ambient_conc"))


def export(mdb):
    table = subprocess.run(["mdb-tables", "-1", mdb], capture_output=True, text=True, check=True).stdout.split("\n")[0]
    tsv = subprocess.run(["mdb-export", "-d", "\t", "-q", "", mdb, table], capture_output=True, text=True, check=True).stdout
    return table, pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str)


def geo(df):
    c = {k.upper(): k for k in df.columns}
    st = df[c["STATE"]].str.strip()
    co = df[c["COUNTY"]].str.strip() if "COUNTY" in c else pd.Series([""] * len(df))
    fips = df[c["FIPS"]].fillna("").str.strip()
    tract = df[c["TRACT"]].fillna("").str.strip()
    pop = pd.to_numeric(df[c.get("POP") or c["POPULATION"]], errors="coerce")
    lvl, gid = [], []
    for s, cn, f, t in zip(st, co, fips, tract):
        if s == "US" or cn.upper() in ("ALL", "WHOLE U.S."):
            lvl.append("nation"); gid.append("00")
        elif t in ("", "-") and (cn.lower() == "state" or len(f) <= 2 or f.endswith("000")):
            lvl.append("state"); gid.append(f[:2])
        elif t in ("", "-"):
            lvl.append("county"); gid.append(f.zfill(5))
        else:
            lvl.append("tract"); gid.append(f.zfill(5) + t.zfill(6))
    return pd.DataFrame({"year": 2005, "geo_level": lvl, "geoid": gid, "state": st, "county": co.to_numpy(),
                         "population": pop}), len([k for k in ("STATE", "COUNTY", "FIPS", "TRACT", "POP", "POPULATION") if k in c])


def classify(col, metric, kind):
    """-> (dimension, name, metric) for one data column."""
    low = col.lower().strip()
    if kind == "category":
        m = next(v for w, v in METRIC_WORDS if w in low)
        if low.startswith("total"):
            return "total", "TOTAL", m
        nm = re.sub(r"(?i)\b(cancer|respiratory|neurological|resp|neur)\b\s*(risk)?\s*-?\s*|\bambient conc\b", "", col).strip(" -")
        return "source_category", nm, m
    if low.startswith("total"):
        return "total", "TOTAL", metric
    first = low.split(" ")[0]
    if first in SECTORS and low.endswith("risk"):
        return "source", col.rsplit(" ", 2)[0].strip(), metric
    return "pollutant", col.strip(), metric


def convert(zpath, metric, kind, out_path):
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zpath) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".mdb")][0]
            z.extract(name, tmp)
        table, df = export(os.path.join(tmp, name))
    base, nid = geo(df)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    writer, rows, stats = None, 0, []
    for col in df.columns[nid:]:  # one data column at a time, appended to the parquet file
        dim, nm, m = classify(col, metric, kind)
        cancer = m == "cancer_risk"
        p = base.copy()
        p["dimension"], p["name"], p["metric"] = dim, nm, m
        p["value"] = pd.to_numeric(df[col], errors="coerce") * (1e6 if cancer else 1.0)
        p["unit"] = "per_million" if cancer else ("ug_m3" if m == "ambient_conc" else "hazard_index")
        p["origin"] = "epa_published"
        p["source_file"] = f"2005/epa/{os.path.basename(zpath)}:{table}"
        p = p[p.value.notna()]
        t = pa.Table.from_pandas(p, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(out_path, t.schema)
        writer.write_table(t.cast(writer.schema))
        rows += len(p)
        stats.append(p.loc[p.geo_level == "tract", ["geoid", "dimension", "metric", "value"]])
    writer.close()
    return rows, pd.concat(stats, ignore_index=True), base


def check(t):
    """EPA total vs sum of sectors / pollutants / categories, tract level."""
    res = {}
    for m, g in t.groupby("metric"):
        tot = g[g.dimension == "total"].set_index("geoid")["value"]
        for dim in ("source", "pollutant", "source_category"):
            s = g[g.dimension == dim].groupby("geoid")["value"].sum()
            if len(s):
                r = (s.reindex(tot.index) / tot - 1).abs()[tot > 0]
                res[f"{m}:{dim}"] = (round(float(r.median()), 6), round(float(r.quantile(0.99)), 6))
    return res


def main(rawdir, outdir):
    for f, (metric, kind) in FILES.items():
        rows, tract, base = convert(os.path.join(rawdir, "2005/epa", f), metric, kind,
                                    os.path.join(outdir, "2005", f.replace(".zip", ".parquet")))
        print(f"ok 2005 {f} long={rows} geo={base.geo_level.value_counts().to_dict()} "
              f"sum-vs-total median/p99 rel err={check(tract)}", flush=True)
        del tract


if __name__ == "__main__":
    main(*sys.argv[1:3])
