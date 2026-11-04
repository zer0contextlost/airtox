"""Build a download manifest of every published NATA/AirToxScreen file (all years).

usage: python -I crawl.py <manifest.tsv>
Output rows: url <TAB> relpath <TAB> source. Re-runnable; only lists, never downloads.
"""
import html
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (airtox archive crawler)"}
FILE_RE = re.compile(r"\.(zip|xlsx?|csv|txt|pdf|docx?|accdb|mdb|kmz|kml|7z|gz|shp|dbf|json)$", re.I)

GAFTP_TREES = [
    ("https://gaftp.epa.gov/rtrmodeling_public/AirToxScreen/2020/", "2020/gaftp"),
    ("https://gaftp.epa.gov/Air/Multipollutant_Platform/2021/", "2021/gaftp"),
]
EPA_SEEDS = [
    "https://www.epa.gov/AirToxScreen",
    "https://www.epa.gov/AirToxScreen/previous-air-toxics-assessments",
    "https://www.epa.gov/national-air-toxics-assessment",
]
EPA_SECTIONS = ("/AirToxScreen", "/national-air-toxics-assessment")
ARCHIVE_SEEDS = [
    "https://archive.epa.gov/nata2002/web/html/index.html",
    "https://archive.epa.gov/airtoxics/nata1999/web/html/index.html",
    "https://archive.epa.gov/airtoxics/nata/web/html/index-2.html",
    "https://archive.epa.gov/airtoxics/nata/web/html/index.html",
]


def get(url, tries=2):
    print(f"GET {url}", flush=True)
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return r.geturl(), r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - crawler: log and move on
            err = e
            time.sleep(2)
    print(f"FAIL {url}: {err}", file=sys.stderr)
    return url, ""


def links(base, text):
    for h in re.findall(r'href\s*=\s*["\']([^"\'#]+)', text, re.I):
        yield urllib.parse.urljoin(base, html.unescape(h.strip()))
    # dropdown menus (onchange=location=...) hold most 1996-2002 data files, often unquoted
    for h in re.findall(r'<option[^>]*value\s*=\s*["\']?([^"\'\s>]+)', text, re.I):
        if not h.startswith(("javascript", "#")):
            yield urllib.parse.urljoin(base, html.unescape(h.strip()))


def year_of(url, page=""):
    """Year from the file name first, then the page path. Upload-date folders (/2018-08/) are ignored."""
    yrs = r"(1996|1999|2002|2005|2011|2014|2017|2018|2019|2020|2021)"
    short = {"96": "1996", "99": "1999", "02": "2002", "05": "2005", "11": "2011", "14": "2014"}
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    for s in (name, urllib.parse.urlparse(page).path):
        m = re.search(r"(?<!\d)" + yrs + r"(?!\d)", s)
        if m:
            return m.group(1)
        m = re.search(r"nata(\d{2})", s.lower())
        if m and m.group(1) in short:
            return short[m.group(1)]
    if "nata1999" in url + page:
        return "1999"
    if "nata2002" in url + page:
        return "2002"
    if "airtoxics/nata/" in url + page:
        return "1996"
    return "misc"


def crawl_gaftp(root, prefix, out):
    stack, seen = [root], {root}
    while stack:
        d = stack.pop()
        _, t = get(d)
        for u in links(d, t):
            # children only: listings link back to their parent, which caused an endless loop
            if not u.startswith(d) or u == d or "?" in u:
                continue
            if u.endswith("/"):
                if u not in seen:
                    seen.add(u)
                    stack.append(u)
            else:
                rel = prefix + "/" + urllib.parse.unquote(u[len(root):])
                out[u] = (rel, "gaftp")


def crawl_site(seeds, allow, depth, out, tag):
    seen, frontier = set(), list(seeds)
    for _ in range(depth + 1):
        nxt = []
        for page in frontier:
            if page in seen:
                continue
            seen.add(page)
            final, t = get(page)
            for u in links(final, t):
                p = urllib.parse.urlparse(u)
                if FILE_RE.search(p.path):
                    if p.netloc.endswith("epa.gov"):
                        name = urllib.parse.unquote(p.path.rsplit("/", 1)[-1])
                        out.setdefault(u, (f"{year_of(u, final)}/{tag}/{name}", tag))
                elif allow(u) and u not in seen:
                    nxt.append(u.split("#")[0])
            time.sleep(0.3)
        frontier = nxt
    return seen


def dump(out, path):
    with open(path, "w") as f:
        for u, (rel, src) in sorted(out.items(), key=lambda kv: kv[1][0]):
            f.write(f"{u}\t{rel}\t{src}\n")
    print(f"WROTE {path} files={len(out)}", flush=True)


def main(path, only=None):
    base = path.rsplit(".", 1)[0]
    arch = {}
    arch_allow = lambda u: u.startswith(("https://archive.epa.gov/nata2002/", "https://archive.epa.gov/airtoxics/nata"))
    crawl_site(ARCHIVE_SEEDS, arch_allow, 3, arch, "archive")
    dump(arch, base + "_archive.tsv")
    if only == "archive":
        return
    epa = {}
    epa_allow = lambda u: urllib.parse.urlparse(u).netloc == "www.epa.gov" and \
        urllib.parse.urlparse(u).path.startswith(EPA_SECTIONS)
    crawl_site(EPA_SEEDS, epa_allow, 2, epa, "epa")
    dump(epa, base + "_epa.tsv")
    ftp = {}
    for root, prefix in GAFTP_TREES:
        crawl_gaftp(root, prefix, ftp)
    dump(ftp, base + "_gaftp.tsv")
    dump({**ftp, **epa, **arch}, path)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "gaftp":
        ftp = {}
        for root, prefix in GAFTP_TREES:
            crawl_gaftp(root, prefix, ftp)
        dump(ftp, sys.argv[1])
    else:
        main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
