#!/usr/bin/env python3
"""Harvest Federal Register rule + proposed-rule metadata into data/docs/<year>.json.

Metadata only -- no full text here. Resumable: a year already on disk is skipped
unless --force. Deep paging stops around 2,000 results on this API regardless of
what `count` claims, so every year is sliced by month and paged within the slice.

    python3 src/harvest.py                 # default window
    python3 src/harvest.py --from 2019 --to 2026
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request, urllib.error

API = "https://www.federalregister.gov/api/v1/documents.json"
HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "data", "docs")

# Everything we need to pair a rulemaking and describe it. Asking for no fields[]
# returns the whole document object for every hit -- large, and mostly URLs.
FIELDS = [
    "document_number", "type", "title", "publication_date", "agencies",
    "regulation_id_numbers", "docket_ids", "raw_text_url", "html_url",
    "comments_close_on", "action", "page_length", "cfr_references",
]

UA = "redline/1.0 (+https://github.com/TNRiley/redline) research build"


def get(url, tries=5):
    """GET with backoff. The API has no published limit but will time out under load."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 404 on a slice with zero results is normal for this API.
            if e.code == 404:
                return {"count": 0, "results": []}
            if attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(2 ** attempt)
    return {"count": 0, "results": []}


def slice_docs(doc_type, start, end):
    """All documents of one type in [start, end], paging until exhausted."""
    out, page = [], 1
    while True:
        q = [("conditions[type][]", doc_type),
             ("conditions[publication_date][gte]", start),
             ("conditions[publication_date][lte]", end),
             ("per_page", "1000"), ("page", str(page)), ("order", "oldest")]
        q += [("fields[]", f) for f in FIELDS]
        d = get(API + "?" + urllib.parse.urlencode(q))
        got = d.get("results") or []
        out.extend(got)
        # per_page maxes at 1000; a short page means the slice is done.
        if len(got) < 1000:
            return out, d.get("count", len(out))
        page += 1
        if page > 2:   # 2,000 is the hard paging ceiling -- month slices never reach it
            sys.stderr.write("    ! slice %s..%s hit the paging ceiling; narrow it\n" % (start, end))
            return out, d.get("count", len(out))


def harvest_year(year, force=False):
    path = os.path.join(DOCS, "%d.json" % year)
    if os.path.exists(path) and not force:
        n = len(json.load(open(path, encoding="utf-8")))
        print("  %d  %5d docs (cached)" % (year, n))
        return
    rows, claimed = [], 0
    for month in range(1, 13):
        start = "%d-%02d-01" % (year, month)
        end = "%d-%02d-01" % (year + 1, 1) if month == 12 else "%d-%02d-01" % (year, month + 1)
        # gte/lte is inclusive on both ends, so step back a day from the next month's first.
        end = _prev_day(end)
        for t in ("RULE", "PRORULE"):
            got, c = slice_docs(t, start, end)
            rows.extend(got)
            claimed += c
        sys.stderr.write("\r  %d  %s  %5d" % (year, start[:7], len(rows)))
        sys.stderr.flush()
    sys.stderr.write("\r" + " " * 40 + "\r")
    os.makedirs(DOCS, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    flag = "" if len(rows) == claimed else "   ! api claimed %d" % claimed
    print("  %d  %5d docs%s" % (year, len(rows), flag))


def _prev_day(iso):
    import datetime
    d = datetime.date(*map(int, iso.split("-"))) - datetime.timedelta(days=1)
    return d.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=2016)
    ap.add_argument("--to", dest="end", type=int, default=2026)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print("harvesting %d-%d" % (a.start, a.end))
    for y in range(a.start, a.end + 1):
        harvest_year(y, a.force)
    total = 0
    for y in range(a.start, a.end + 1):
        p = os.path.join(DOCS, "%d.json" % y)
        if os.path.exists(p):
            total += len(json.load(open(p, encoding="utf-8")))
    print("total %d documents" % total)


if __name__ == "__main__":
    main()
