"""Block-level cancer risk by pollutant and by source group from EPA's per-pollutant EXPCONC CSVs.

usage: python -I risk_csv.py <year 2020|2021> <region 1-10> <out_dir>
Streams one pollutant zip at a time (low memory, unlike the region xlsx). Risk (per million) =
EC x URE x 1e6, URE from EPA's 2020 pollutant table (= EPA's table for 2018-2020, see README).
Linear in EC, so it splits over EPA's source-group columns the same way.

PAHPOM (lumped POM) has no single URE. 2020: EPA's published PAHPOM risk is used as is and every
other pollutant is checked against EPA's per-pollutant RISK CSV. 2021: PAHPOM is estimated per block
and source group as EC_2021 x (EPA risk_2020 / EC_2020) for the same block and source group,
falling back to the block's total 2020 ratio, then the county median, then the region median.

Writes <out_dir>/<year>_R<n>_by_poll.parquet and _by_src.parquet, plus a JSON summary.
"""
import glob
import io
import json
import math
import os
import re
import resource
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

RAW = os.environ.get("AIRTOX_RAW", "/srv/airtox/raw")  # EPA raw mirror, layout as written by download.py
DIRS = {"2020": f"{RAW}/2020/gaftp/Pollutant Summaries", "2021": f"{RAW}/2021/gaftp/Ambient_Exposure_Concentrations_by_Pollutant"}
TOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ref", "2020AirToxScreenPollutants.xlsx")
ALIASES = {"BENZOAPYRNE": "BENZOAPYRENE", "BIS2ETHYLHEXYLPHTHALATE": "BIS2ETHYLHEXYLPHTHALATEDEHP"}
# 2020 source-group names -> 2021 names (2021 also adds TRANSPORT and NP-FirePileBurns)
SRC20 = {"NR-CMV_C1C2_ports": "NR-CMV_C1_C2_ports", "NP-StorageTransfer_BulkTerminals_GasStage1 Cancer Risk":
         "NP-StorageTransfer_BulkTerminals_GasStage1", "NP-AgricultureLivestock (Waste)": "NP-AgricultureLivestock",
         "NP-AgricultureLivestock (Silage)": "NP-AgricultureSilage"}
IDS = ["State", "County", "FIPS", "Block", "Population"]
SUFFIXES = ("Cancer Risk (per million)", "Exposure Conc", "Ambient Conc")
SKIP = {"State", "EPA Region", "County", "FIPS", "Block", "Population", "Pollutant Name"}


def norm(s):
    k = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    return ALIASES.get(k, k)


def sig1(x):
    return 0.0 if x == 0 else round(x, -int(math.floor(math.log10(abs(x)))))


def load_ure():
    tox = pd.read_excel(TOX, engine="openpyxl")
    ure = {}
    for _, r in tox.iterrows():
        u = r["URE 1/(ug/m3)"]
        if pd.notna(u) and u > 0:
            ure.setdefault(norm(r["AirToxScreen WEBSITE POLL NAME (comment)"]), float(u))
    return ure


def requote(f):
    """Re-quote County and Pollutant Name when unquoted commas split them. Layout: State, EPA Region, County,
    FIPS (5 digits), Block (15 digits), Population, Pollutant Name, then a fixed number of numeric columns."""
    lines = io.TextIOWrapper(f, encoding="utf-8", newline="")
    header = next(lines).rstrip("\r\n")
    nnum = len(header.split(",")) - 7
    buf = io.BytesIO()
    buf.write((header + "\n").encode())
    for line in lines:
        x = line.rstrip("\r\n").split(",")
        if len(x) == nnum + 7:
            buf.write((",".join(x) + "\n").encode())
            continue
        fi = next(i for i in range(3, len(x)) if re.fullmatch(r"\d{5}", x[i]) and re.fullmatch(r"\d{15}", x[i + 1]))
        county, rest = ",".join(x[2:fi]), x[fi:]
        name, nums = ",".join(rest[3:len(rest) - nnum]), rest[len(rest) - nnum:]
        q = lambda v: '"' + v.replace('"', '""') + '"'
        buf.write((",".join(x[:2] + [q(county)] + rest[:3] + [q(name)] + nums) + "\n").encode())
    buf.seek(0)
    return buf


def read_zip(path, suffix):
    """-> (DataFrame of id cols + 'Total' + source groups, pollutant name). Strips the metric suffix."""
    with zipfile.ZipFile(path) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        opts = pacsv.ConvertOptions(column_types={"FIPS": "string", "Block": "string", "State": "string", "County": "string"})
        try:
            with z.open(name) as f:
                t = pacsv.read_csv(io.BufferedReader(f, 1 << 22), convert_options=opts)
        except pa.ArrowInvalid:  # EPA's 2021 Region 10 CSVs leave commas in names unquoted ("1,3-BUTADIENE")
            with z.open(name) as f:
                t = pacsv.read_csv(requote(f), convert_options=opts)
    df = t.to_pandas(self_destruct=True, split_blocks=True)
    del t
    # EPA's 2020 Region 9 files drop the leading zero on some CA/AZ block IDs, differently per file
    df["Block"], df["FIPS"] = df["Block"].str.zfill(15), df["FIPS"].str.zfill(5)
    poll = str(df["Pollutant Name"].iloc[0])
    ren = {}
    for c in df.columns:
        if c in SKIP:
            continue
        # strip any metric suffix: EPA's 2020 PAHPOM_RISK_R3.csv carries risk values under "Exposure Conc" headers
        s = next((c[: -len(x)] for x in (suffix,) + SUFFIXES if c.endswith(x)), c).strip()
        ren[c] = "Total" if s.lower().startswith("total") else SRC20.get(s, s)
    df = df.drop(columns=["EPA Region", "Pollutant Name"]).rename(columns=ren)
    return df, poll


def align(df, block_index):
    if len(df) == len(block_index) and (df["Block"].to_numpy() == block_index).all():
        return df
    return df.set_index("Block").reindex(block_index).reset_index()


def main(year, region, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ure = load_ure()
    zips = sorted(glob.glob(f"{DIRS[year]}/Region{region}/*_EXPCONC_R{region}.zip"))
    ids = None
    by_poll, src_acc, src_names, pah_ec, check = {}, None, [], None, []
    for zp in zips:
        df, poll = read_zip(zp, "Exposure Conc")
        key = norm(poll)
        if ids is None:
            ids = df[IDS].copy()
            blocks = ids["Block"].to_numpy()
        else:
            df = align(df, blocks)
        srcs = [c for c in df.columns if c not in IDS and c != "Total"]
        if key == "PAHPOM":
            pah_ec = df[["Total"] + srcs].copy()
            del df
            continue
        if key not in ure:
            del df
            continue
        u = ure[key] * 1e6
        by_poll[poll] = df["Total"].to_numpy(float) * u
        if src_acc is None:
            src_names, src_acc = list(srcs), np.zeros((len(blocks), len(srcs)))
        for c in srcs:
            if c not in src_names:  # source group first seen in a later file
                src_names.append(c)
                src_acc = np.hstack([src_acc, np.zeros((len(blocks), 1))])
            src_acc[:, src_names.index(c)] += df[c].to_numpy(float) * u
        if year == "2020":  # exact check vs EPA's own per-pollutant risk file
            rz = zp.replace("_EXPCONC_", "_RISK_")
            if os.path.exists(rz):
                epa, _ = read_zip(rz, "Cancer Risk (per million)")
                epa = align(epa, blocks)
                cols = ["Total"] + [c for c in srcs if c in epa.columns]
                ours = np.concatenate([by_poll[poll]] + [df[c].to_numpy(float) * u for c in cols[1:]])
                theirs = np.concatenate([epa[c].to_numpy(float) for c in cols])
                ok = np.abs(theirs) > 1e-12
                rel = np.abs(ours[ok] / theirs[ok] - 1) if ok.any() else np.array([0.0])
                check.append({"pollutant": poll, "max_rel_err": float(rel.max()), "p99": float(np.percentile(rel, 99)),
                              "ours_zero_epa_not": int(((ours == 0) & ok).sum())})
        del df
        print(f"R{region} {year} {poll}", flush=True)
    srcs = src_names

    # PAHPOM
    pah_src = pd.DataFrame(0.0, index=range(len(blocks)), columns=srcs)
    fb_risk = np.zeros(len(blocks))  # PAHPOM risk estimated without a same-source 2020 ratio
    if year == "2020":
        epa, _ = read_zip(f"{DIRS['2020']}/Region{region}/PAHPOM_RISK_R{region}.zip", "Cancer Risk (per million)")
        epa = align(epa, blocks)
        pah_total = epa["Total"].to_numpy(float)
        for c in srcs:
            if c in epa.columns:
                pah_src[c] = epa[c].to_numpy(float)
        pah_origin = "epa_published"
    else:
        r20, _ = read_zip(f"{DIRS['2020']}/Region{region}/PAHPOM_RISK_R{region}.zip", "Cancer Risk (per million)")
        e20, _ = read_zip(f"{DIRS['2020']}/Region{region}/PAHPOM_EXPCONC_R{region}.zip", "Exposure Conc")
        r20, e20 = align(r20, blocks), align(e20, blocks)
        with np.errstate(divide="ignore", invalid="ignore"):
            tot_ratio = np.where(e20["Total"].to_numpy(float) > 0, r20["Total"].to_numpy(float) / e20["Total"].to_numpy(float), np.nan)
            cty = pd.Series(tot_ratio).groupby(ids["FIPS"].to_numpy()).transform("median").to_numpy()
            reg = np.nanmedian(tot_ratio)
            blk = np.where(np.isfinite(tot_ratio), tot_ratio, np.where(np.isfinite(cty), cty, reg))
            fb_blk = np.where(np.isfinite(tot_ratio), 1, np.where(np.isfinite(cty), 2, 3)).astype("int8")
            for c in srcs:
                ec = pah_ec[c].to_numpy(float) if c in pah_ec.columns else np.zeros(len(blocks))
                if c in r20.columns and c in e20.columns:
                    e = e20[c].to_numpy(float)
                    r = np.where(e > 0, r20[c].to_numpy(float) / e, np.nan)
                else:
                    r = np.full(len(blocks), np.nan)
                use = np.where(np.isfinite(r), r, blk)
                pah_src[c] = ec * use
                fb_risk += np.where(np.isfinite(r), 0.0, ec * use)
        pah_total = pah_src.sum(axis=1).to_numpy()
        # sources in the PAHPOM EC file the cancer pollutants never had
        extra = [c for c in pah_ec.columns if c not in srcs and c != "Total"]
        if extra:
            print("WARN PAHPOM-only source columns ignored:", extra, flush=True)
        pah_origin = "estimated_from_2020_block_ratio"
        del r20, e20

    # build output tables straight from the numpy columns (no DataFrame copies; R4/R5 are ~1.2M blocks)
    exact = np.zeros(len(blocks))
    for v in by_poll.values():
        exact += v
    total = exact + pah_total
    idcols = {c: pa.array(ids[c].to_numpy()) for c in IDS}
    cols = dict(idcols)
    cols.update({p: pa.array(v) for p, v in by_poll.items()})
    cols.update({"PAHPOM": pa.array(pah_total), "total_excl_pahpom": pa.array(exact),
                 "total_cancer_risk_per_million": pa.array(total),
                 "total_rounded_epa_style": pa.array(np.array([sig1(x) for x in total]))})
    if year == "2021":
        with np.errstate(divide="ignore", invalid="ignore"):
            cols["pahpom_share_no_source_ratio"] = pa.array(np.where(pah_total > 0, fb_risk / pah_total, 0.0))
        cols["pahpom_ratio_level"] = pa.array(fb_blk)  # 1 block's own 2020 total ratio, 2 county median, 3 region median
    pq.write_table(pa.table(cols), f"{out_dir}/{year}_R{region}_by_poll.parquet")
    del cols

    cols, src_sum = dict(idcols), np.zeros(len(blocks))
    for c in srcs:
        v = src_acc[:, srcs.index(c)] + pah_src[c].to_numpy()
        src_sum += v
        cols[c] = pa.array(v)
    cols["total_cancer_risk_per_million"] = pa.array(total)
    pq.write_table(pa.table(cols), f"{out_dir}/{year}_R{region}_by_src.parquet")
    del cols

    pop = ids["Population"].to_numpy(float)
    gap = np.abs(src_sum - total)
    summ = {"year": year, "region": region, "blocks": int(len(blocks)), "population": float(pop.sum()),
            "pollutants": len(by_poll), "source_groups": len(srcs), "pahpom_origin": pah_origin,
            "popwtd_total": float(np.average(total, weights=pop)),
            "popwtd_pahpom": float(np.average(pah_total, weights=pop)),
            "src_vs_poll_max_abs_gap": float(gap.max()),
            "pahpom_popwtd_share_no_source_ratio": float(np.average(fb_risk, weights=pop) / max(np.average(pah_total, weights=pop), 1e-30)),
            "pahpom_ratio_level_blocks": ({int(k): int(v) for k, v in zip(*np.unique(fb_blk, return_counts=True))} if year == "2021" else None),
            "maxrss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
            "check_vs_epa": sorted(check, key=lambda d: -d["max_rel_err"])[:10] if check else None}
    json.dump(summ, open(f"{out_dir}/{year}_R{region}_summary.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in summ.items() if k != "check_vs_epa"}), flush=True)
    if check:
        print("worst vs EPA:", json.dumps(summ["check_vs_epa"][:5]), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
