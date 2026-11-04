"""Convert NATA 1999 Access databases to the long schema used by the other harmonize_*.py scripts.

usage: python -I harmonize_1999.py <rawdir> <outdir>
Inputs under <rawdir>/1999/archive/airtoxics/nata1999/web/:
- hap1mdb/, hap2mdb/: one tract table per pollutant (table CAA_NUM_<n>) with ASPEN ambient conc, HAPEM
  exposure conc, cancer risk (per million) and/or noncancer hazard quotient, each by source sector + total.
  -> <outdir>/1999/tract/<caa_num>_<name>.parquet (one file per pollutant, like 1996).
- statemdb/: per-state tract totals for cancer risk, respiratory and neurological HI by sector, plus each
  pollutant's percent of the tract total. -> 1999/{cancer_risk,respiratory_hi,neurological_hi}_tract.parquet
- countymdb/: county emissions (tons/yr) by sector per pollutant -> 1999/county_emissions.parquet
- cntymod/: county distribution of tract ambient concs per pollutant -> 1999/county_conc_distribution.parquet
Pollutant names, CAS and target organs come from EPA's mdb/caa_num_xref_nata99_mod1feb05.mdb.
"""
import glob
import io
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

WEB = "1999/archive/airtoxics/nata1999/web"
SECTOR = {"major": "major", "areaother": "area_other", "area": "area_other", "onroad": "onroad",
          "nonroad": "nonroad", "background": "background"}
PREFIX = (("aspen", "ambient_conc", "ug_m3"), ("hapem", "exposure_conc", "ug_m3"),
          ("risk", "cancer_risk", "per_million"), ("hq", "hazard_quotient", "hazard_quotient"))


def tables(mdb):
    return [t for t in subprocess.run(["mdb-tables", "-1", mdb], capture_output=True, text=True, check=True).stdout.split("\n") if t]


def export(mdb, table):
    tsv = subprocess.run(["mdb-export", "-d", "\t", "-q", "", mdb, table], capture_output=True, text=True, check=True).stdout
    return pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str)


def xref(raw):
    x = export(f"{raw}/{WEB}/mdb/caa_num_xref_nata99_mod1feb05.mdb", "CAA_NUM_info")
    return {str(r.CAA_num).strip(): r for r in x.itertuples()}


def tract_geo(df):
    tid = df["tract_id"].str.strip()
    base = pd.DataFrame({"year": 1999, "geo_level": "tract", "geoid": tid, "state": df["st"].str.strip(),
                         "county": df["county"].str.strip(),
                         "population": pd.to_numeric(df["tract_population"], errors="coerce")})
    base["urban_rural"] = df["uflag"].str.strip()
    return base


def melt(base, df, cols_meta, src):
    """cols_meta: list of (column, dimension, name, metric, unit) -> long DataFrame."""
    parts = []
    for col, dim, nm, metric, unit in cols_meta:
        p = base.copy()
        p["dimension"], p["name"], p["metric"] = dim, nm, metric
        p["value"] = pd.to_numeric(df[col], errors="coerce")
        p["unit"], p["origin"], p["source_file"] = unit, "epa_published", src
        parts.append(p[p.value.notna()])
    return pd.concat(parts, ignore_index=True)


def classify_tract_col(col):
    low = col.lower().replace("_per_million", "")
    for pre, metric, unit in PREFIX:
        if low.startswith(pre):
            rest = low[len(pre):]
            if rest == "total":
                return "total", "TOTAL", metric, unit
            if rest in SECTOR:
                return "source", SECTOR[rest], metric, unit
    return None


def hap_tracts(raw, out, xr):
    os.makedirs(f"{out}/1999/tract", exist_ok=True)
    checks = []
    for mdb in sorted(glob.glob(f"{raw}/{WEB}/hap1mdb/*.mdb") + glob.glob(f"{raw}/{WEB}/hap2mdb/*.mdb")):
        t = tables(mdb)[0]
        key = t.split("CAA_NUM_", 1)[1]
        num = re.match(r"\d+", key).group(0) if re.match(r"\d+", key) else key.lower()
        info = xr.get(num) if key.isdigit() else None
        # two tables don't follow the CAA_NUM_<n> pattern: CAA_NUM_174_arsine (arsine, split out of the
        # arsenic group 174) and CAA_NUM_DieselPM (not a CAA HAP)
        pname = {"174_arsine": "Arsine", "DieselPM": "Diesel particulate matter"}.get(key) or (
            info.CAA_HAPname_Description if info is not None else os.path.basename(mdb).replace("_nata99.mdb", ""))
        slug = re.sub(r"[^a-z0-9]+", "_", str(pname).lower()).strip("_")[:60]
        dest = f"{out}/1999/tract/{num.zfill(3)}_{slug}.parquet"
        if os.path.exists(dest):
            long = pd.read_parquet(dest)
        else:
            long = None
        if long is None:
            df = export(mdb, t)
            base = tract_geo(df)
            base["cas"] = info.cas if info is not None else None
            meta = []
            for c in df.columns[5:]:
                k = classify_tract_col(c)
                if k is None:
                    print(f"  WARN unknown column {c} in {mdb}", flush=True)
                    continue
                dim, nm, metric, unit = k
                meta.append((c, dim, nm, metric, unit))
            long = melt(base, df, meta, f"{os.path.relpath(mdb, raw)}:{t}")
            long.insert(long.columns.get_loc("dimension"), "pollutant", pname)
            pq.write_table(pa.Table.from_pandas(long, preserve_index=False), dest)
        # EPA total vs sum of sectors, per metric
        for metric, g in long.groupby("metric"):
            w = g.pivot_table(index="geoid", columns="dimension", values="value", aggfunc="sum")
            if {"total", "source"} <= set(w.columns):
                m = w.total > 0
                checks.append((pname, metric, float((w.source[m] / w.total[m] - 1).abs().quantile(0.99)) if m.any() else 0.0))
        print(f"ok 1999 tract {num} {pname} rows={len(long)} metrics={sorted(long.metric.unique())}", flush=True)
    c = pd.DataFrame(checks, columns=["pollutant", "metric", "p99_rel_err_sectors_vs_total"])
    c.to_csv(f"{out}/1999/tract_sector_sum_check.csv", index=False)
    print("sector-sum check p99 rel err by metric (max over pollutants):",
          c.groupby("metric").p99_rel_err_sectors_vs_total.max().round(4).to_dict(), flush=True)
    bad = c[c.p99_rel_err_sectors_vs_total > 0.01]
    if len(bad):
        print(bad.to_string(index=False), flush=True)


STATE_TABLES = {"Risk_totals": ("cancer_risk_tract", "cancer_risk", "per_million"),
                "Respiratory_totals": ("respiratory_hi_tract", "hazard_index_respiratory", "hazard_index"),
                "Neurological_totals": ("neurological_hi_tract", "hazard_index_neurological", "hazard_index")}


def state_totals(raw, out):
    for table, (fname, metric, unit) in STATE_TABLES.items():
        dest = f"{out}/1999/{fname}.parquet"
        if os.path.exists(dest):
            continue
        writer, rows, tracts, sums = None, 0, 0, []
        for mdb in sorted(glob.glob(f"{raw}/{WEB}/statemdb/*_risks_nata99.mdb")):  # one state at a time
            df = export(mdb, table)
            base = tract_geo(df)
            meta = []
            for c in df.columns[5:]:
                low = c.lower().replace("_per_million", "")
                low = re.sub(r"^(risk|hq)", "", low)
                if low == "total":
                    meta.append((c, "total", "TOTAL", metric, unit))
                elif low in SECTOR:
                    meta.append((c, "source", SECTOR[low], metric, unit))
                else:  # pollutant columns are that pollutant's percent of the tract total
                    meta.append((c, "pollutant", c.replace("_", " ").strip(), metric + "_percent_of_total", "percent"))
            pol = [m[0] for m in meta if m[1] == "pollutant"]
            s = df[pol].apply(pd.to_numeric, errors="coerce").sum(axis=1)
            tot = pd.to_numeric(df[[m[0] for m in meta if m[1] == "total"][0]], errors="coerce")
            sums.append(s[tot > 0])
            t = pa.Table.from_pandas(melt(base, df, meta, f"{os.path.relpath(mdb, raw)}:{table}"), preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(dest, t.schema)
            writer.write_table(t.cast(writer.schema))
            rows, tracts = rows + t.num_rows, tracts + len(df)
            del df, t
        writer.close()
        s = pd.concat(sums)
        print(f"ok 1999 {fname} rows={rows} tracts={tracts} "
              f"pollutant-percent sums: median={s.median():.1f} p1={s.quantile(.01):.1f} p99={s.quantile(.99):.1f}", flush=True)


def county_emissions(raw, out):
    parts = []
    for mdb in sorted(glob.glob(f"{raw}/{WEB}/countymdb/*.mdb")):
        for t in tables(mdb):
            df = export(mdb, t)
            if "pollutant" not in df.columns:
                continue
            fips = df["fips"].str.strip()
            lvl = np.where(fips == "N/A", "nation", np.where(fips.str.len() <= 2, "state", "county"))
            base = pd.DataFrame({"year": 1999, "geo_level": lvl, "geoid": np.where(fips == "N/A", "00", fips),
                                 "state": df["state"].str.strip(), "county": df["county"].str.strip(), "population": np.nan})
            base["urban_rural"] = df["urban_rural"].str.strip()
            base["pollutant"] = df["pollutant"].str.strip()
            base["cas"] = None
            meta = [(c, "total" if c == "total" else "source", "TOTAL" if c == "total" else SECTOR.get(c, c),
                     "emissions", "tons_per_year") for c in ("major", "area", "onroad", "nonroad", "total") if c in df.columns]
            parts.append(melt(base, df, meta, f"{os.path.relpath(mdb, raw)}:{t}"))
    long = pd.concat(parts, ignore_index=True)
    pq.write_table(pa.Table.from_pandas(long, preserve_index=False), f"{out}/1999/county_emissions.parquet")
    print(f"ok 1999 county_emissions rows={len(long)} pollutants={long.pollutant.nunique()}", flush=True)


def county_dist(raw, out):
    parts = []
    for mdb in sorted(glob.glob(f"{raw}/{WEB}/cntymod/*.mdb")):
        if "county_distributions" not in tables(mdb):
            continue
        df = export(mdb, "county_distributions")
        fips = df["fips"].str.strip()
        lvl = np.where(fips == "N/A", "nation", np.where(fips.str.len() <= 2, "state", "county"))
        base = pd.DataFrame({"year": 1999, "geo_level": lvl, "geoid": np.where(fips == "N/A", "00", fips),
                             "state": df["state"].str.strip(), "county": df["county"].str.strip(), "population": np.nan})
        base["urban_rural"] = df["urban_rural_flag"].str.strip()
        base["pollutant"], base["cas"] = df["HAP"].str.strip(), df["CAS"].str.strip()
        meta = []
        for c in df.columns[6:]:
            if c.endswith("_mean"):
                s = c[: -len("_mean")]
                meta.append((c, "total" if s == "total" else "source", "TOTAL" if s == "total" else SECTOR.get(s.replace("_", ""), s),
                             "ambient_conc_tract_mean", "ug_m3"))
            else:
                meta.append((c, "statistic", c, "ambient_conc_tract_distribution", "ug_m3"))
        parts.append(melt(base, df, meta, f"{os.path.relpath(mdb, raw)}:county_distributions"))
    long = pd.concat(parts, ignore_index=True)
    pq.write_table(pa.Table.from_pandas(long, preserve_index=False), f"{out}/1999/county_conc_distribution.parquet")
    print(f"ok 1999 county_conc_distribution rows={len(long)} pollutants={long.pollutant.nunique()}", flush=True)


def main(raw, out):
    os.makedirs(f"{out}/1999", exist_ok=True)
    hap_tracts(raw, out, xref(raw))
    state_totals(raw, out)
    county_emissions(raw, out)
    county_dist(raw, out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
