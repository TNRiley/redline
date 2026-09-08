#!/usr/bin/env python3
"""Fetch the full text of every document in the text cohort into data/text/<n>.txt.

Resumable -- one file per document, an existing file is never re-fetched. Run it
again after an interruption and it picks up where it stopped.

Downloading is delegated to curl, which handles keep-alive, parallelism, retry and
backoff properly without any of it being written here.

Three things about the endpoint that are not documented anywhere:

* `raw_text_url` ends in `.txt` and serves **HTML**: `<html><head><title>...</title>
  </head><body><pre>` wrapping the text, with `</pre></body></html>` after it. Read it
  as plain text and every document carries a header that is not part of the rule.
* The payload contains stray NUL bytes, so `grep` calls the saved file binary and
  skips it silently -- which looks exactly like "no matches" rather than an error.
* **The host rate-limits full-text downloads, and says so with 429.** This one cost
  the most. A first benchmark, run before anything had been hammered, suggested well
  over a thousand documents a minute. Every measurement after it was an order of
  magnitude slower, which read like a client-side performance problem and sent this
  script through two rewrites -- thread pools, connection pooling, curl -- before
  anyone checked the status code. It was 429 the whole time. Being rate-limited is
  not being broken: ~8 concurrent transfers is sustainable at roughly 60 documents a
  minute, and the corpus is sized to what that allows rather than to what would be
  ideal.

Both are handled in `clean()`, once, so nothing downstream has to know.

    python3 src/fetch_text.py              # everything in cohort.json
    python3 src/fetch_text.py --limit 500
"""
import argparse, json, os, random, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
COHORT = os.path.join(HERE, "data", "cohort.json")
TEXT = os.path.join(HERE, "data", "text")
UA = "redline/1.0 (+https://github.com/TNRiley/redline) research build"

_PRE = re.compile(r"<pre>(.*)</pre>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
BATCH = 400          # URLs per curl invocation; keeps the config file small
SHUFFLE_SEED = 20260907


def clean(raw):
    """HTML wrapper off, NULs out, CRLF normalised. Everything else is left alone --
    the whitespace in these documents is meaningful for section detection."""
    m = _PRE.search(raw)
    body = m.group(1) if m else _TAG.sub("", raw)
    body = body.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return body.strip("\n")


def pending(cohort):
    """Documents in the cohort with no file yet, de-duplicated: one proposal can be
    the parent of several finals.

    Shuffled, with a fixed seed. This matters more than it looks: the cohort is
    sorted by date, the rate limit means a run is often stopped part-way, and a
    prefix of a date-sorted list is every rule from 2016 to 2019 and none after. A
    seeded shuffle makes any partial fetch a random sample of the whole period
    instead, and makes the same partial fetch reproducible."""
    seen, todo = set(), []
    for p in cohort:
        for side in ("final", "proposed"):
            dn, url = p[side], p[side + "_text_url"]
            if url and dn not in seen and not os.path.exists(os.path.join(TEXT, dn + ".txt")):
                seen.add(dn)
                todo.append((dn, url))
    random.Random(SHUFFLE_SEED).shuffle(todo)
    return todo


def fetch_batch(jobs, workdir, parallel):
    """One curl run over a batch. Returns the raw files it actually produced."""
    cfg = os.path.join(workdir, "urls.txt")
    with open(cfg, "w", encoding="utf-8") as f:
        for dn, url in jobs:
            # Forward slashes, always. Inside a curl config file a backslash is an
            # escape character, so a Windows path written literally is mangled into
            # something curl cannot open -- and with --silent it fails quietly,
            # leaving a batch of empty files that look like genuine 404s.
            out = os.path.join(workdir, dn + ".raw").replace("\\", "/")
            f.write('url = "%s"\noutput = "%s"\n' % (url, out))
    r = subprocess.run(
        ["curl", "--parallel", "--parallel-max", str(parallel), "--silent",
         "--show-error", "--fail", "--retry", "4", "--retry-delay", "2",
         "--retry-all-errors", "--max-time", "120", "-A", UA, "-K", cfg],
        capture_output=True, text=True)
    return ({dn: os.path.join(workdir, dn + ".raw") for dn, _ in jobs},
            (r.stderr or "").count("429"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--parallel", type=int, default=8,
                    help="concurrent curl transfers. The host rate-limits: 16 at a "
                         "time earns a wall of 429s. 8 with retry is sustainable.")
    a = ap.parse_args()

    if not shutil.which("curl"):
        raise SystemExit("curl not found on PATH -- it does the downloading here")

    cohort = json.load(open(COHORT, encoding="utf-8"))
    os.makedirs(TEXT, exist_ok=True)
    todo = pending(cohort)
    if a.limit:
        todo = todo[:a.limit]
    print("%d documents to fetch, %d at a time" % (len(todo), a.parallel))
    if not todo:
        return

    ok = miss = throttled = 0
    t0 = time.time()
    work = tempfile.mkdtemp(prefix="redline-")
    try:
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            got, n429 = fetch_batch(batch, work, a.parallel)
            throttled += n429
            for dn, raw_path in got.items():
                try:
                    raw = open(raw_path, encoding="utf-8", errors="replace").read()
                except OSError:
                    raw = ""
                # An empty file records the miss, so a re-run does not retry forever.
                body = clean(raw) if raw.strip() else ""
                tmp = os.path.join(TEXT, dn + ".part")
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(body)
                os.replace(tmp, os.path.join(TEXT, dn + ".txt"))
                if body:
                    ok += 1
                else:
                    miss += 1
                try:
                    os.remove(raw_path)
                except OSError:
                    pass
            done = ok + miss
            el = time.time() - t0
            rate = done / el if el else 0
            sys.stderr.write("\r  %d/%d  ok %d  missing %d  %.0f/min  eta %dm   "
                             % (done, len(todo), ok, miss, rate * 60,
                                (len(todo) - done) / rate / 60 if rate else 0))
            sys.stderr.flush()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    sys.stderr.write("\n")
    print("fetched %d, missing %d, in %.1f min (%d requests were rate-limited and retried)"
          % (ok, miss, (time.time() - t0) / 60, throttled))


if __name__ == "__main__":
    main()
