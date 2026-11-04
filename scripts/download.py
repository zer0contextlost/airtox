"""Download every file in the manifest into /srv/airtox/raw, resumable, oldest years first.

usage: python -I download.py <manifest.tsv> <rawdir>
Skips files already complete (size matches server Content-Length). Writes <rawdir>/../files.tsv
(relpath, bytes, sha256, url, fetched_utc) and progress to stdout for journalctl.
"""
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (airtox archive)"}
ORDER = ["1996", "1999", "2002", "2005", "2011", "2014", "2017", "2018", "2019", "misc", "2020", "2021"]


def head_len(url):
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length") or -1)
    except Exception:  # noqa: BLE001
        return -1


def fetch(url, dest):
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    hdr = dict(UA)
    if have:
        hdr["Range"] = f"bytes={have}-"
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=120) as r:
        mode = "ab" if have and r.status == 206 else "wb"
        with open(part, mode) as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
    os.replace(part, dest)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def main(manifest, rawdir):
    rows = [l.rstrip("\n").split("\t") for l in open(manifest) if l.strip()]
    rows.sort(key=lambda r: (ORDER.index(r[1].split("/")[0]) if r[1].split("/")[0] in ORDER else 99, r[1]))
    log_path = os.path.join(os.path.dirname(rawdir.rstrip("/")), "files.tsv")
    done = {l.split("\t")[0] for l in open(log_path)} if os.path.exists(log_path) else set()
    failed = 0
    for i, (url, rel, _src) in enumerate(rows, 1):
        if rel in done:
            continue
        dest = os.path.join(rawdir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        gone = False
        for attempt in range(6):
            try:
                fetch(url, dest)
                break
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):  # dead link: record it, don't wait on retries
                    gone = True
                    break
                print(f"[{i}/{len(rows)}] retry {attempt + 1} {rel}: {e}", flush=True)
                time.sleep(20 * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(rows)}] retry {attempt + 1} {rel}: {e}", flush=True)
                time.sleep(20 * (attempt + 1))
        else:
            failed += 1
            print(f"[{i}/{len(rows)}] FAILED {rel}", flush=True)
            continue
        if gone:
            with open(os.path.join(os.path.dirname(log_path), "missing.tsv"), "a") as f:
                f.write(f"{rel}\t{url}\t404\t{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
            print(f"[{i}/{len(rows)}] MISSING(404) {rel}", flush=True)
            continue
        size = os.path.getsize(dest)
        expect = head_len(url)
        if expect > 0 and expect != size:
            print(f"[{i}/{len(rows)}] SIZE MISMATCH {rel} {size} != {expect}", flush=True)
            failed += 1
            continue
        with open(log_path, "a") as f:
            f.write(f"{rel}\t{size}\t{sha256(dest)}\t{url}\t{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        print(f"[{i}/{len(rows)}] ok {rel} {size / 1e6:.1f} MB", flush=True)
    print(f"DONE failed={failed}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(*sys.argv[1:3])
