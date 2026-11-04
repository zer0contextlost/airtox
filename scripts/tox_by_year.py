"""The cancer unit risk estimate (URE) EPA applied to each pollutant in each assessment year, next to the 2020 value.

usage: python -I tox_by_year.py <out.csv>
Where EPA published exposure concentrations next to risk, the URE is recovered from EPA's own numbers
(risk / exposure, median over tracts, with the spread reported): 1996, 1999, 2002, 2018, 2019.
Otherwise it is read from that year's documentation: 2005 (health effects "Table 1"), 2011 (TSD Appendix H),
2014 and 2017 (EPA pollutant spreadsheets). Matched to the 2020 table by CAS, then by name.
Output columns: year, pollutant, cas, ure_year, how, spread, ure_2020, pollutant_2020, factor (= ure_2020 / ure_year).
"""
import glob
import io
import os
import re
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_csv import TOX, norm  # noqa: E402

RAW = os.environ.get("AIRTOX_RAW", "/srv/airtox/raw")
PUB = "/srv/airtox/derived/epa_published"
NUM = r"[0-9]*\.?[0-9]+(?:[eE]-?\d+)?"


def cas_norm(c):
    c = re.sub(r"[^0-9A-Za-z]", "", str(c)).lstrip("0")
    return c or None


def key(name):
    """Looser name key for matching across years' naming styles."""
    s = norm(re.sub(r"\(.*?\)", "", str(name)))
    s = re.sub(r"(COMPOUNDS|ANDSALTS|SALTSANDESTERS|INORGANIC|MAYINCLUDEARSINE|ALLISOMERS)", "", s)
    return {"POLYCYCLICORGANICMATTER": "PAHPOM", "POM": "PAHPOM", "BENZYLCHLORIDE": "BENZYLCHLORIDE",
            "BENZYCHLORIDE": "BENZYLCHLORIDE", "CHROMIUMVI": "CHROMIUMVIHEXAVALENT",
            "HEXAVALENTCHROMIUM": "CHROMIUMVIHEXAVALENT"}.get(s, s)


def ref2020():
    t = pd.read_excel(TOX, engine="openpyxl")
    t["u"] = pd.to_numeric(t["URE 1/(ug/m3)"], errors="coerce")
    t = t[t.u > 0]
    by_cas, by_key = {}, {}
    for _, r in t.iterrows():
        nm = r["AirToxScreen WEBSITE POLL NAME (comment)"]
        c = cas_norm(r["Pollutant Code (CAS)"])
        if c:
            by_cas.setdefault(c, (nm, float(r.u)))
        by_key.setdefault(key(nm), (nm, float(r.u)))
        by_key.setdefault(key(r["NEI Pollutant Description"]), (nm, float(r.u)))
    return by_cas, by_key


def table_xlsx(path, namecol, year):
    d = pd.read_excel(path, sheet_name=0)
    casc = next(c for c in d.columns if "CAS" in str(c))
    rows = []
    for _, r in d.iterrows():
        u = pd.to_numeric(r["URE 1/(ug/m3)"], errors="coerce")
        if pd.notna(u) and u > 0:
            rows.append((year, r[namecol], cas_norm(r[casc]), float(u), "epa_table", np.nan))
    return rows


def fixed_width_pdf(text, start_marker, max_ure_col, year, how):
    """Rows with a CAS number; the first number left of max_ure_col after the CAS (and WOE code) is the URE."""
    lines = text.split("\n")
    s = next(i for i, l in enumerate(lines) if start_marker in l)
    rows = []
    for l in lines[s:]:
        m = re.search(r"\s(\d{4,9})\s+", l)
        if not m or not l[: m.start()].strip():
            continue
        u = None
        # standalone numbers only: skips weight-of-evidence codes like "B2" and footnote digits glued to words
        for mm in re.finditer(r"(?<![A-Za-z0-9.])" + NUM + r"(?![A-Za-z0-9])", l[m.end():]):
            pos = m.end() + mm.start()
            if pos < max_ure_col and re.fullmatch(NUM, mm.group()):
                u = float(mm.group())
                break
        if u:
            rows.append((year, l[: m.start()].strip(), cas_norm(m.group(1)), u, how, np.nan))
    return rows


def pdftext(path):
    return subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout


def inferred(year, name, cas, risk, ec, scale):
    """URE from EPA's own risk and exposure columns, per tract."""
    risk, ec = np.asarray(risk, float), np.asarray(ec, float)
    m = (risk > 0) & (ec > 0)
    if m.sum() < 20:
        return None
    r = risk[m] / ec[m] * scale
    med = float(np.median(r))
    return (year, name, cas_norm(cas), med, "inferred_from_epa_risk_and_exposure",
            float((np.percentile(r, 99) - np.percentile(r, 1)) / med))


def y1996():
    rows = []
    for f in sorted(glob.glob(f"{PUB}/1996/*-tract.parquet")):
        t = pq.read_table(f, columns=["geoid", "dimension", "pollutant", "cas", "metric", "value"],
                          filters=[("dimension", "=", "total")]).to_pandas()
        w = t.pivot_table(index="geoid", columns="metric", values="value", aggfunc="first")
        if {"cancer_risk", "exposure_conc"} <= set(w.columns):
            r = inferred(1996, t.pollutant.iloc[0], t.cas.iloc[0], w.cancer_risk, w.exposure_conc, 1e-6)
            if r:
                rows.append(r)
    return rows


def y1999():
    rows = []
    for f in sorted(glob.glob(f"{PUB}/1999/tract/*.parquet")):
        t = pq.read_table(f, columns=["geoid", "pollutant", "cas", "dimension", "metric", "value"],
                          filters=[("dimension", "=", "total")]).to_pandas()
        w = t.pivot_table(index="geoid", columns="metric", values="value", aggfunc="first")
        if {"cancer_risk", "exposure_conc"} <= set(w.columns):
            r = inferred(1999, t.pollutant.iloc[0], t.cas.iloc[0], w.cancer_risk, w.exposure_conc, 1e-6)
            if r:
                rows.append(r)
    return rows


def y2002():
    rows = []
    for mdb in sorted(glob.glob(f"{RAW}/2002/archive/nata2002/web/tractmdb/pollutantmdb/*.mdb")):
        tab = subprocess.run(["mdb-tables", "-1", mdb], capture_output=True, text=True).stdout.split("\n")[0]
        if not tab:
            continue
        tsv = subprocess.run(["mdb-export", "-d", "\t", "-q", "", mdb, tab], capture_output=True, text=True).stdout
        d = pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str)
        cols = {c.upper(): c for c in d.columns}
        if "AVGRSKTOT" not in cols or "AVGEXPTOT" not in cols:
            continue
        tract = d[cols["TRACT"]].fillna("").str.strip()
        d = d[(tract != "") & (tract != "-")]
        r = inferred(2002, tab, None, pd.to_numeric(d[cols["AVGRSKTOT"]], errors="coerce"),
                     pd.to_numeric(d[cols["AVGEXPTOT"]], errors="coerce"), 1.0)
        if r:
            rows.append(r)
    return rows


def y2005():
    txt = pdftext(f"{RAW}/2002/archive/nata2002/web/pdf/health_effects.pdf")  # describes the 2005 assessment
    return fixed_width_pdf(txt, "Table 1: Dose-Responses Values", 72, 2005, "epa_document_table")


def y2011():
    txt = pdftext(f"{RAW}/2011/epa/2011-nata-tsd.pdf")
    return fixed_width_pdf(txt, "Exhibit H-1. Toxicity Values Used in the 2011 NATA", 60, 2011, "epa_document_table")


def unzip_one(zpath, member_suffix, dest):
    with zipfile.ZipFile(zpath) as z:
        n = [m for m in z.namelist() if m.endswith(member_suffix)][0]
        p = os.path.join(dest, os.path.basename(n))
        with open(p, "wb") as f:
            f.write(z.read(n))
    return p


def main(out_csv):
    tmp = "/tmp/airtox_tox"
    os.makedirs(tmp, exist_ok=True)
    rows = y1996() + y1999() + y2002() + y2005() + y2011()
    rows += table_xlsx(unzip_one(f"{RAW}/2014/epa/2014_nata_supplemental_data_files_0.zip", "NATA_Pollutants.xlsx", tmp),
                       "NATA WEBSITE POLL NAME (or comment)", 2014)
    rows += table_xlsx(unzip_one(f"{RAW}/2017/epa/2017-airtoxscreen-supplemental-data-files.zip",
                                 "2017AirToxScreenPollutants.xlsx", tmp), "AirToxScreen WEBSITE POLL NAME (or comment)", 2017)
    for y in (2018, 2019):
        t = pd.read_csv(f"/srv/airtox/derived/tox/tox_inferred_{y}.csv")
        for _, r in t.dropna(subset=["ure_inferred"]).iterrows():
            rows.append((y, r.pollutant, None, float(r.ure_inferred), "inferred_from_epa_risk_and_exposure", r.ure_spread))
    d = pd.DataFrame(rows, columns=["year", "pollutant", "cas", "ure_year", "how", "spread"])
    by_cas, by_key = ref2020()
    m = [by_cas.get(c) if c else None for c in d.cas]
    m = [x or by_key.get(key(p)) for x, p in zip(m, d.pollutant)]
    d["pollutant_2020"] = [x[0] if x else None for x in m]
    d["ure_2020"] = [x[1] if x else 0.0 for x in m]
    d["factor"] = d.ure_2020 / d.ure_year
    d = d.drop_duplicates(["year", "pollutant", "cas"])
    d.to_csv(out_csv, index=False)
    for y, g in d.groupby("year"):
        chg = g[(g.factor - 1).abs() > 0.02]
        print(f"{y}: {len(g)} carcinogens ({g.how.iloc[0]}), unmatched to 2020 = {int(g.pollutant_2020.isna().sum())}, "
              f"changed >2% = {len(chg)}", flush=True)
        for _, r in chg.sort_values("factor").iterrows():
            print(f"    {r.pollutant[:45]:45s} {r.ure_year:.3g} -> {r.ure_2020:.3g}  x{r.factor:.3g}"
                  + (f"  spread {r.spread:.3f}" if pd.notna(r.spread) else ""), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:2])
