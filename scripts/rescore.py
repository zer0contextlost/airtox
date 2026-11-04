"""Re-score every year's EPA tract cancer risk with the 2020 toxicity values (phase 4).

usage: python -I rescore.py <ure_by_year.csv> <out_dir>
For each year: rescored = EPA's published tract total + sum over pollutants of (factor - 1) x EPA's risk for that
pollutant, factor = URE_2020 / URE_year from tox_by_year.py. Pollutants EPA no longer scores for cancer get factor 0.
Not rescored (factor kept at 1, listed in the output): lumped groups and metal compounds (POM/PAH groups, chromium,
nickel, PCBs), where EPA folds speciation or group-specific values into the number and one ratio would be wrong.
Writes <out_dir>/<year>_tract_rescored.parquet (geoid, population, published total, rescored total, change by
pollutant) and national/state population-weighted summaries.
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tox_by_year import key  # noqa: E402

PUB = "/srv/airtox/derived/epa_published"
KEEP = re.compile(r"^(PAHPOM|POM|POLYCYCLIC|CHROMIUM|NICKEL|PCB|POLYCHLORINATED)")


def factors(tox, year):
    t = tox[tox.year == year]
    out, kept = {}, []
    for _, r in t.iterrows():
        k = key(r.pollutant)
        f = r.factor if pd.notna(r.factor) else 1.0
        if KEEP.match(k) and r.ure_2020 > 0:
            if abs(f - 1) > 0.02:
                kept.append(f"{r.pollutant} (x{f:.3g} not applied)")
            f = 1.0
        if r.ure_2020 == 0 and KEEP.match(k):  # 1999 POM sub-groups: still scored in 2020 as part of PAHPOM
            f = 1.0
        out[k] = float(f)
    return out, kept


def batches(f, metric="cancer_risk"):
    """Stream tract rows of one EPA long table as small DataFrames: geoid (int), population, dimension, name, value."""
    names = pq.read_schema(f).names
    # these tables were written as one big row group: keep strings dictionary-encoded while streaming
    pf = pq.ParquetFile(f, read_dictionary=[c for c in ("geo_level", "metric", "geoid", "dimension", "name", "pollutant") if c in names])
    cols = [c for c in ("geo_level", "metric", "geoid", "population", "dimension", "name", "pollutant", "value") if c in names]
    for b in pf.iter_batches(batch_size=500_000, columns=cols):
        m = pc.and_(pc.equal(b["geo_level"], "tract"), pc.equal(b["metric"], metric))
        if "pollutant" in names:  # 1996/1999 per-pollutant files: total rows carry the pollutant's risk
            m = pc.and_(m, pc.equal(b["dimension"], "total"))
        b = b.filter(m)
        if b.num_rows == 0:
            continue
        t = b.to_pandas()
        for c in ("geoid", "dimension", "name", "pollutant"):
            if c in t.columns:
                t[c] = t[c].astype(str)
        if "pollutant" in names:
            t["dimension"], t["name"] = "pollutant", t["pollutant"]
        t = t[t.geoid.str.fullmatch(r"\d+")]
        yield pd.DataFrame({"geoid": t.geoid.astype("int64"), "population": t.population.astype("float64"),
                            "dimension": t.dimension, "name": t.name, "value": t.value.astype("float64")})


def year_sources(y):
    """-> (files with pollutant rows, file with the EPA total rows or None = sum of pollutants, skip-name prefix)."""
    if y == 1996:
        return sorted(glob.glob(f"{PUB}/1996/*-tract.parquet")), None, None
    if y == 1999:
        return sorted(glob.glob(f"{PUB}/1999/tract/*.parquet")), f"{PUB}/1999/cancer_risk_tract.parquet", "pom_group"
    files = {2002: [f"{PUB}/2002/cancer_risk_tract.parquet"], 2005: [f"{PUB}/2005/us_nata05_v4_srcrisk_bytract.parquet"]}.get(y) \
        or sorted(glob.glob(f"{PUB}/{y}/*cancerrisk_by_tract_poll.parquet"))
    return files, files[0], None


def accumulate(y, f):
    """Stream one year: EPA total, population, delta from rescoring, per-pollutant change, by geoid (int)."""
    files, total_file, skip = year_sources(y)
    deltas, sums, pops, chs, unmatched, kcache = [], [], [], None, set(), {}
    for fpath in files:
        for t in batches(fpath):
            pt = t[t.dimension == "pollutant"]
            if skip:
                pt = pt[~pt.name.str.lower().str.startswith(skip)]
            if pt.empty:
                continue
            for nm in pt.name.unique():
                if nm not in kcache:
                    kcache[nm] = f.get(key(nm))
            fac = pt.name.map(kcache)
            unmatched |= set(pt.name[fac.isna() & (pt.value > 0)])
            fac = fac.fillna(1.0).astype(float)
            d = (fac - 1.0) * pt.value
            deltas.append(d.groupby(pt.geoid).sum())
            sums.append(pt.value.groupby(pt.geoid).sum())
            pops.append(pt.groupby("geoid").population.first())
            c = pt.assign(d=d)[fac != 1.0]
            if len(c):  # running sum: concatenating every batch's pivot first runs out of memory on 2002
                pv = c.pivot_table(index="geoid", columns="name", values="d", aggfunc="sum")
                chs = pv if chs is None else chs.add(pv, fill_value=0.0)
    delta = pd.concat(deltas).groupby(level=0).sum()
    polsum = pd.concat(sums).groupby(level=0).sum()
    pop = pd.concat(pops).groupby(level=0).first()
    changes = chs if chs is not None else pd.DataFrame()
    if total_file:
        tots, tpops = [], []
        for t in batches(total_file):
            tt = t[t.dimension == "total"]
            tots.append(tt.groupby("geoid").value.first())
            tpops.append(tt.groupby("geoid").population.first())
        tot = pd.concat(tots).groupby(level=0).first()
        pop = pop.combine_first(pd.concat(tpops).groupby(level=0).first())
    else:
        tot = polsum
    return tot, pop, delta, changes, sorted(unmatched)


def main(tox_csv, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tox = pd.read_csv(tox_csv)
    years = [int(a) for a in sys.argv[3:]] or [1996, 1999, 2002, 2005, 2011, 2014, 2017, 2018, 2019]
    prev = f"{out_dir}/rescored_summary.json"
    summary = [s for s in (json.load(open(prev)) if os.path.exists(prev) else []) if s["year"] not in years]
    for y in years:
        f, kept = factors(tox, y)
        tot, pop, delta, changes, unmatched = accumulate(y, f)
        out = pd.DataFrame({"population": pop, "total_published": tot})
        out = out.join(delta.rename("delta")).fillna({"delta": 0.0})
        out["total_rescored_2020tox"] = (out.total_published + out.delta).clip(lower=0)
        out = out.join(changes.add_prefix("change_"))
        out.index = out.index.astype(str).str.zfill(11)
        out.index.name = "geoid"
        out.reset_index().to_parquet(f"{out_dir}/{y}_tract_rescored.parquet", index=False)
        o = out.dropna(subset=["total_published", "population"])
        o = o[o.population > 0]
        w = o.population
        cc = o[[c for c in o.columns if c.startswith("change_")]]
        top = (cc.mul(w, axis=0).sum() / w.sum()).rename(lambda c: c[len("change_"):]).sort_values()
        s = {"year": y, "tracts": int(len(o)), "population": float(w.sum()),
             "popwtd_published": float(np.average(o.total_published, weights=w)),
             "popwtd_rescored_2020tox": float(np.average(o.total_rescored_2020tox, weights=w)),
             "biggest_changes_per_million": {k: round(float(v), 3) for k, v in pd.concat([top.head(4), top.tail(4)]).items() if abs(v) > 0.005},
             "groups_not_rescored": kept, "pollutants_without_toxicity_match": unmatched}
        summary.append(s)
        print(json.dumps(s), flush=True)
        json.dump(sorted(summary, key=lambda s: s["year"]), open(prev, "w"), indent=1)
    json.dump(sorted(summary, key=lambda s: s["year"]), open(prev, "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:3])
