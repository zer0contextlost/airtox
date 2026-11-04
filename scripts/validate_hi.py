"""Check the noncancer method against EPA's published 2019 tract hazard indexes before using it on 2021.

usage: python -I validate_hi.py <out.json>
HQ = exposure conc (ug/m3) / (RfC (mg/m3) x 1000); organ HI = sum of HQ over pollutants flagged for that organ
in EPA's 2020 pollutant table. Compares (a) per-pollutant HQ in EPA's five per-organ tract files,
(b) which pollutants EPA counts for each organ, (c) per-organ tract HI in EPA's all-organ file.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_csv import TOX, norm  # noqa: E402

PUB = "/srv/airtox/derived/epa_published/2019"
RAW = os.environ.get("AIRTOX_RAW", "/srv/airtox/raw")
ORGANS = {"resp": "Respiratory", "liver": "Liver", "neuro": "Neurological", "dev": "Developmental",
          "reprod": "Reproductive", "kidney": "Kidney", "ocular": "Ocular", "endoc": "Endocrine",
          "hemato": "Hematological", "immune": "Immunological", "skeletal": "Skeletal", "spleen": "Spleen",
          "thyroid": "Thyroid", "wholebod": "Whole Body"}
FILES = {"resp": "resphi", "neuro": "neurhi", "immune": "immuhi", "kidney": "kidneyhi", "liver": "liverhi"}


def load_tox():
    """-> {norm name: (rfc_mg, set(organ keys))}, first row per website name with an RfC (as risk.py does for URE)."""
    t = pd.read_excel(TOX, engine="openpyxl")
    out = {}
    for _, r in t.iterrows():
        rfc = r["RFC (mg/m3)"]
        if pd.notna(rfc) and rfc > 0:
            organs = {k for k in ORGANS if isinstance(r.get(k), str) and r.get(k).strip()}
            out.setdefault(norm(r["AirToxScreen WEBSITE POLL NAME (comment)"]), (float(rfc), organs))
    return out


def tracts(path, **kw):
    t = pq.read_table(path, filters=[("geo_level", "=", "tract")], **kw).to_pandas()
    return t


def main(out_json):
    tox = load_tox()
    ec = pd.read_excel(f"{RAW}/2019/epa/2019_Toxics_Exposure_Concentrations.xlsx", engine="calamine", dtype={3: str, 4: str})
    tr = ec.iloc[:, 4].astype(str)
    ec = ec[~tr.str.endswith("000000")]
    ec.index = ec.iloc[:, 4].astype(str)
    ecn = {norm(c): c for c in ec.columns[5:]}
    hq = {k: pd.to_numeric(ec[ecn[k]], errors="coerce") / (rfc * 1000.0) for k, (rfc, _) in tox.items() if k in ecn}
    res = {"pollutants_with_rfc_and_ec": len(hq), "per_organ_file": {}, "all_organ_file": {}}

    for organ, stem in FILES.items():
        epa = tracts(f"{PUB}/2019_national_{stem}_by_tract_poll.parquet", columns=["geoid", "dimension", "name", "value"])
        polls = epa[epa.dimension == "pollutant"]
        epa_set = {norm(n) for n in polls.name.unique()}
        our_set = {k for k, (_, o) in tox.items() if organ in o and k in ecn}
        worst, n, worst_name, worst_p99 = 0.0, 0, None, 0.0
        for name, g in polls.groupby("name"):
            k = norm(name)
            if k not in hq:
                continue
            ours = hq[k].reindex(g.geoid.to_numpy()).to_numpy()
            theirs = g.value.to_numpy()
            m = np.abs(theirs) > 1e-12
            if m.any():
                e = np.abs(ours[m] / theirs[m] - 1)
                if np.nanmax(e) > worst:
                    worst, worst_name, worst_p99 = float(np.nanmax(e)), name, float(np.nanpercentile(e, 99))
                n += 1
        res["per_organ_file"][organ] = {"epa_pollutants": len(epa_set), "our_flagged": len(our_set),
                                        "epa_not_flagged_by_us": sorted(epa_set - our_set),
                                        "flagged_by_us_not_in_epa": sorted(our_set - epa_set),
                                        "pollutants_compared": n, "max_rel_err_hq": worst,
                                        "worst_pollutant": worst_name, "worst_pollutant_p99_rel_err": worst_p99}

    allhi = tracts(f"{PUB}/2019_national_allhi_bytract.parquet", columns=["geoid", "name", "value"])
    for organ, label in ORGANS.items():
        g = allhi[allhi.name == label]
        if g.empty:
            res["all_organ_file"][organ] = "not in EPA file"
            continue
        keys = [k for k, (_, o) in tox.items() if organ in o and k in hq]
        ours = sum(hq[k].fillna(0) for k in keys).reindex(g.geoid.to_numpy()).to_numpy()
        theirs = g.value.to_numpy()
        # EPA rounds HI; accept if ours rounds to EPA's value at EPA's precision (1 significant figure)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.where(theirs > 0, 10.0 ** np.floor(np.log10(np.abs(theirs))), 1e-6)
        ok = np.abs(ours - theirs) <= 0.5 * mag + 1e-12
        res["all_organ_file"][organ] = {"tracts": int(len(g)), "pollutants": len(keys), "within_rounding": float(ok.mean()),
                                        "median_rel_err": float(np.nanmedian(np.abs(ours[theirs > 0] / theirs[theirs > 0] - 1)))}
    json.dump(res, open(out_json, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:2])
