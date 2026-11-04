"""Convert NATA 2002 national tract databases (cancer, respiratory, neurological) to the long schema.

usage: python -I harmonize_2002.py <rawdir> <outdir>
Reads the three national zips under <rawdir>/2002/archive/nata2002/web/zip/, exports each Access table
tab-separated with mdbtools, and writes <outdir>/2002/<name>.parquet in the same columns as
harmonize_wide.py. Cancer risk is stored by EPA as a probability; converted to per million (x1e6).
"""
import io
import os
import subprocess
import sys
import tempfile
import zipfile

import pandas as pd

FILES = {
    "cancer_risk_tract.zip": ("cancer_risk", "per_million", 1e6),
    "respiratory_risk_tract.zip": ("hazard_index_respiratory", "hazard_index", 1.0),
    "neurological_risk_tract.zip": ("hazard_index_neurological", "hazard_index", 1.0),
}
SOURCE_COLS = ("major source", "area source", "on-road", "non-road", "background")


def export(mdb):
    table = subprocess.run(["mdb-tables", "-1", mdb], capture_output=True, text=True, check=True).stdout.split("\n")[0]
    tsv = subprocess.run(["mdb-export", "-d", "\t", "-q", "", mdb, table], capture_output=True, text=True, check=True).stdout
    return table, pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str)


def convert(zpath, metric, unit, scale, out_path):
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zpath) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".mdb")][0]
            z.extract(name, tmp)
        table, df = export(os.path.join(tmp, name))
    c = {k.upper(): k for k in df.columns}
    st, co, fips, tract, pop = (df[c[k]] for k in ("STATE", "COUNTY", "FIPS", "TRACT", "POP"))
    lvl, gid = [], []
    for s, cn, f, t in zip(st, co, fips, tract):
        t = "" if pd.isna(t) else str(t).strip()
        if str(cn).strip().upper() == "ALL":
            lvl.append("nation"); gid.append("00")
        elif t in ("", "-") and str(cn).strip().lower() == "state":
            lvl.append("state"); gid.append(str(f)[:2])
        elif t in ("", "-"):
            lvl.append("county"); gid.append(str(f).zfill(5))
        else:
            lvl.append("tract"); gid.append(str(f).zfill(5) + t.zfill(6))
    base = pd.DataFrame({"year": 2002, "geo_level": lvl, "geoid": gid, "state": st.str.strip(),
                         "county": co.str.strip(), "population": pd.to_numeric(pop, errors="coerce")})
    parts = []
    for col in df.columns[5:]:
        low = col.lower()
        if low.startswith("total"):
            dim, nm = "total", "TOTAL"
        elif low.startswith(SOURCE_COLS):
            dim, nm = "source", col.replace(" Risk", "").replace(" Hazard", "").strip()
        else:
            dim, nm = "pollutant", col.strip()
        p = base.copy()
        p["dimension"], p["name"], p["metric"] = dim, nm, metric
        p["value"] = pd.to_numeric(df[col], errors="coerce") * scale
        p["unit"], p["origin"] = unit, "epa_published"
        p["source_file"] = f"2002/archive/nata2002/web/zip/{os.path.basename(zpath)}:{table}"
        parts.append(p)
    out = pd.concat(parts, ignore_index=True)
    out = out[out.value.notna()]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_parquet(out_path, index=False)
    return len(df), len(out), out.loc[out.dimension == "pollutant", "name"].nunique()


def main(rawdir, outdir):
    zdir = os.path.join(rawdir, "2002/archive/nata2002/web/zip")
    for f, (metric, unit, scale) in FILES.items():
        out = os.path.join(outdir, "2002", f.replace(".zip", ".parquet"))
        rows, longrows, polls = convert(os.path.join(zdir, f), metric, unit, scale, out)
        print(f"ok 2002 {f} rows={rows} long={longrows} pollutants={polls}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
