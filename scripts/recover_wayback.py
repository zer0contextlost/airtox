"""Recover files EPA now 404s (/srv/airtox/missing.tsv) from Wayback Machine captures.

usage: python -I recover_wayback.py <cdx_dir> <airtox_root>
<cdx_dir> holds Wayback CDX listings (fl=original,timestamp,mimetype,length) for the old NATA paths.
Candidates must share the file's basename and parent directory name; the payload must look like the
right file type (PDF / Jet DB magic), not an HTML error page. Recovered files go to raw/<rel> like any
other download, are appended to files.tsv with the Wayback URL as source, and to recovered.tsv.
"""
import glob
import hashlib
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (airtox archive)"}
MAGIC = {".pdf": [b"%PDF"], ".mdb": [b"\x00\x01\x00\x00Standard Jet DB", b"\x00\x01\x00\x00Standard ACE DB"]}


def load_cdx(cdx_dir):
    idx = {}
    for f in glob.glob(os.path.join(cdx_dir, "cdx_*.txt")):
        for line in open(f):
            p = line.split()
            if len(p) < 4 or "html" in p[2]:
                continue
            path = p[0].split("?")[0].lower()
            idx.setdefault(os.path.basename(path), []).append((path, p[1], p[0]))
    return idx


def candidates(idx, rel):
    base = os.path.basename(rel).lower()
    parent = os.path.basename(os.path.dirname(rel)).lower()
    c = [x for x in idx.get(base, []) if os.path.basename(os.path.dirname(x[0])) == parent]
    return sorted(c, key=lambda x: x[1], reverse=True)  # newest capture first


def main(cdx_dir, root):
    idx = load_cdx(cdx_dir)
    done = {l.split("\t")[0] for l in open(os.path.join(root, "files.tsv"))}
    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(root, "missing.tsv")) if l.strip()]
    got = still = 0
    for rel, url, *_ in rows:
        if rel in done:  # recovered already (missing.tsv repeats rows across manifests)
            continue
        ext = os.path.splitext(rel)[1].lower()
        ok = False
        for _path, ts, orig in candidates(idx, rel):
            wb = f"https://web.archive.org/web/{ts}id_/{orig}"
            try:
                with urllib.request.urlopen(urllib.request.Request(wb, headers=UA), timeout=180) as r:
                    data = r.read()
            except Exception as e:  # noqa: BLE001
                print(f"  fail {rel} {ts}: {e}", flush=True)
                time.sleep(10)
                continue
            if not any(data.startswith(m) for m in MAGIC.get(ext, [b""])):
                print(f"  bad payload {rel} {ts} ({data[:20]!r})", flush=True)
                continue
            dest = os.path.join(root, "raw", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            sha = hashlib.sha256(data).hexdigest()
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with open(os.path.join(root, "files.tsv"), "a") as f:
                f.write(f"{rel}\t{len(data)}\t{sha}\t{wb}\t{now}\n")
            with open(os.path.join(root, "recovered.tsv"), "a") as f:
                f.write(f"{rel}\t{len(data)}\t{sha}\twayback\t{wb}\t{url}\t{now}\n")
            print(f"ok {rel} {len(data) / 1e6:.2f} MB from {ts}", flush=True)
            ok = True
            break
        if ok:
            got += 1
            done.add(rel)
        else:
            still += 1
            print(f"NOT FOUND {rel}", flush=True)
        time.sleep(2)  # be polite to the Wayback Machine
    print(f"DONE recovered={got} still_missing={still}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
