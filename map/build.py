"""Build the national county map: cancer risk per million, every assessment year 1996-2021.

usage: venv/bin/python -I map/build.py   (run from airtox/)
Writes map/data.js. Reads release parquet only (column-selected, one year at a time).
"""
import io
import json
import math
import zipfile

import numpy as np
import pandas as pd
import shapefile
from shapely.geometry import shape

REL = "/srv/airtox/release/data"
YEARS_OLD = [1996, 1999, 2002, 2005, 2011, 2014, 2017, 2018, 2019]
# Retired/renamed county FIPS -> current (2020) FIPS
FIPS_FIX = {"12025": "12086", "46113": "46102", "02270": "02158", "51515": "51019", "51560": "51005",
            "51780": "51083", "02201": "02198", "02232": "02230", "02280": "02195", "08014": "08013",
            "30113": "30067"}


def county_avg(df, val_cols):
    df = df[df.population > 0].copy()
    df["cfips"] = df.geoid.str[:5].map(lambda f: FIPS_FIX.get(f, f))
    for c in val_cols:
        df[c + "_w"] = df[c] * df.population
    g = df.groupby("cfips")
    out = pd.DataFrame({"pop": g.population.sum()})
    for c in val_cols:
        out[c] = g[c + "_w"].sum() / out["pop"]
    return out


def natl(df, col):
    df = df[df.population > 0]
    return float(np.average(df[col], weights=df.population))


series = {}   # year -> {fips: [published, rescored]}
national = {}
for y in YEARS_OLD:
    d = pd.read_parquet(f"{REL}/rescored_2020tox/{y}_tract_rescored.parquet",
                        columns=["geoid", "population", "total_published", "total_rescored_2020tox"])
    c = county_avg(d, ["total_published", "total_rescored_2020tox"])
    series[y] = {f: [round(r.total_published, 1), round(r.total_rescored_2020tox, 1)] for f, r in c.iterrows()}
    national[y] = [round(natl(d, "total_published"), 1), round(natl(d, "total_rescored_2020tox"), 1)]
    print(y, len(c), national[y], flush=True)

# 2020 (EPA, block-level, rolled up) and 2021 (recomputed): same toxicity table, so rescored == published.
detail = {}   # fips -> {year: {drivers, sources}}
names = {}
SRC_GROUPS = {"PT-": "Industrial & point sources", "OR-": "Cars & trucks", "NR-": "Off-road engines",
              "NP-": "Small & area sources", "FIRES": "Fires", "BIOGENICS": "Plants (natural)",
              "SECONDARY": "Formed in the air", "BACKGROUND": "Background", "AIRPORT": "Airports",
              "RAIL": "Rail", "CMV": "Ships"}


def src_bucket(col):
    u = col.upper()
    for k, v in SRC_GROUPS.items():
        if u.startswith(k) or k in u:
            return v
    return "Other"


def pname(k, y):
    if k == "PAHPOM":
        return "PAH/POM" + (" (estimated)" if y == 2021 else "")
    return k.title().replace("Iii", "III").replace("Vi)", "VI)")


for y in (2020, 2021):
    p = pd.read_parquet(f"{REL}/rollups/{y}_county_by_poll.parquet")
    s = pd.read_parquet(f"{REL}/rollups/{y}_county_by_src.parquet")
    meta = {"geoid", "state", "population", "blocks", "county"}
    tot = "total_cancer_risk_per_million"
    polls = [c for c in p.columns if c not in meta and not c.startswith("total")]
    srcs = [c for c in s.columns if c not in meta and not c.startswith("total")]
    p = p[p.population > 0]
    series[y] = {f: [round(v, 1), round(v, 1)] for f, v in zip(p.geoid, p[tot])}
    national[y] = [round(float(np.average(p[tot], weights=p.population)), 1)] * 2
    print(y, len(p), national[y], "src cols", sorted({src_bucket(c) for c in srcs}), flush=True)
    for r in p.itertuples(index=False):
        names[r.geoid] = f"{r.county}, {r.state}"
    pv = p.set_index("geoid")[polls]
    for f, row in pv.iterrows():
        top = row.sort_values(ascending=False).head(5)
        detail.setdefault(f, {})[y] = {"drivers": [[pname(k, y), round(float(v), 2)] for k, v in top.items() if v >= 0.05]}
    sv = s[s.population > 0].set_index("geoid")[srcs]
    buckets = sv.T.groupby(sv.columns.map(src_bucket)).sum().T
    for f, row in buckets.iterrows():
        tot_s = row.sum()
        if f in detail and y in detail[f] and tot_s > 0:
            detail[f][y]["sources"] = [[k, round(float(v / tot_s * 100))] for k, v in
                                       row.sort_values(ascending=False).items() if v / tot_s >= 0.03]

# --- geometry: Albers equal-area (USGS CONUS params), AK/HI/PR insets ---
def albers(lon, lat, lon0=-96, lat0=23, p1=29.5, p2=45.5):
    lon, lat = np.radians(lon), np.radians(lat)
    lon0, lat0, p1, p2 = map(math.radians, (lon0, lat0, p1, p2))
    n = (math.sin(p1) + math.sin(p2)) / 2
    C = math.cos(p1) ** 2 + 2 * n * math.sin(p1)
    r0 = math.sqrt(C - 2 * n * math.sin(lat0)) / n
    r = np.sqrt(C - 2 * n * np.sin(lat)) / n
    t = n * (lon - lon0)
    return r * np.sin(t), r0 - r * np.cos(t)


INSET = {  # state fips -> (albers params, scale vs CONUS, dx, dy in CONUS units)
    "02": (dict(lon0=-154, lat0=50, p1=55, p2=65), 0.35, -0.24, -0.01),
    "15": (dict(lon0=-157, lat0=13, p1=8, p2=18), 1.0, -0.08, -0.08),
    "72": (dict(lon0=-66, lat0=18, p1=8, p2=18), 2.0, 0.29, 0.05),
}
z = zipfile.ZipFile("data/geo/cb_2020_us_county_20m.zip")
base = "cb_2020_us_county_20m"
sf = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")))
fields = [f[0] for f in sf.fields[1:]]
gi, ni, si = fields.index("GEOID"), fields.index("NAME"), fields.index("STUSPS")
raw = []
for sr in sf.iterShapeRecords():
    g = shape(sr.shape.__geo_interface__)
    if sr.record[gi][:2] in ("60", "66", "69", "78"):
        continue
    raw.append((sr.record[gi], f"{sr.record[ni]}, {sr.record[si]}", g))


def proj_ring(coords, st):
    a = np.array(coords)
    a[:, 0] = np.where(a[:, 0] > 0, a[:, 0] - 360, a[:, 0])  # Aleutians past the antimeridian
    if st in INSET:
        prm, k, dx, dy = INSET[st]
        x, y = albers(a[:, 0], a[:, 1], **prm)
        return x * k + dx, y * k + dy
    return albers(a[:, 0], a[:, 1])


# Valdez-Cordova (02261) split into Chugach/Copper River in 2019: show the old value on both.
for y in series:
    if "02261" in series[y]:
        v = series[y].pop("02261")
        series[y].setdefault("02063", v)
        series[y].setdefault("02066", v)

projected = []
for fips, name, g in raw:
    polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
    rings = []
    for pg in polys:
        for rg in [pg.exterior] + list(pg.interiors):
            rings.append(proj_ring(rg.coords, fips[:2]))
    projected.append((fips, name, rings))
allx = np.concatenate([r[0] for _, _, rs in projected for r in rs])
ally = np.concatenate([r[1] for _, _, rs in projected for r in rs])
W = 960.0
S = W / (allx.max() - allx.min())
H = (ally.max() - ally.min()) * S
X0, Y1 = allx.min(), ally.max()

counties = []
for fips, name, rings in projected:
    d = ""
    for x, y in rings:
        px, py = (x - X0) * S, (Y1 - y) * S
        if len(px) < 3:
            continue
        d += "M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in zip(px, py)) + "Z"
    vals = {str(y): series[y].get(fips) for y in series}
    counties.append(dict(id=fips, name=names.get(fips, name), d=d,
                         v={k: v for k, v in vals.items() if v is not None}, x=detail.get(fips, {})))

missing = {y: sorted(set(series[y]) - {c["id"] for c in counties}) for y in series}
print("counties drawn", len(counties), "data fips not on map:", {y: len(m) for y, m in missing.items()},
      {y: m[:8] for y, m in missing.items() if m})
out = dict(width=W, height=round(H, 1), years=sorted(series), national={str(k): v for k, v in national.items()},
           counties=counties)
with open("map/data.js", "w") as fh:
    fh.write("window.AIRTOX=" + json.dumps(out, separators=(",", ":")) + ";\n")
print("wrote map/data.js")
