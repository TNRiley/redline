#!/usr/bin/env python3
"""Build the gzipped payload the page reads, and splice it into index.html.

Three layers ship, and they are different sizes on purpose:

* `pop`  -- every paired rulemaking, columnar, metadata only. ~14k rows, tiny.
* `rules` -- every diffed rulemaking's numbers. Thousands of rows, still small.
* `red`  -- the actual redlines. These are the expensive part: the changed
  paragraphs of a rule, both sides, in full. Shipping them for every diffed rule
  would be well over a hundred megabytes, so a reading set is selected -- stratified
  across the revision range so the page is browsable rather than a highlight reel --
  and everything else links out to the Federal Register's own text.

Columnar, not row objects: repeating twenty key names across fourteen thousand rows
costs more than the values do. Gzip hides some of that, but not the parse cost.

    python3 src/build_payload.py
"""
import base64, datetime, gzip, io, json, os, random, re, shutil, statistics, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
TEMPLATE = os.path.join(HERE, "template.html")
OUT = os.path.abspath(os.path.join(HERE, "..", "index.html"))

READING_SET = 420          # rules whose full redline ships
MAX_BLOCKS = 70            # changed blocks kept per shipped redline
MAX_PARA = 1400            # characters kept per shipped paragraph
SEED = 20260907


def load(name):
    p = os.path.join(D, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def days(a, b):
    try:
        da = datetime.date(*map(int, a.split("-")))
        db = datetime.date(*map(int, b.split("-")))
        return (db - da).days
    except Exception:
        return None


def trim_blocks(blocks):
    """Keep the changed blocks, drop the word-level detail past a point, and record
    how much was left out so the page can say so rather than pretend it is complete."""
    out, kept_changed, dropped = [], 0, 0
    for b in blocks:
        if b["t"] == "=":
            out.append({"t": "=", "n": b["n"]})
            continue
        if kept_changed >= MAX_BLOCKS:
            dropped += 1
            continue
        kept_changed += 1
        nb = {"t": b["t"]}
        for side in ("a", "b"):
            if side in b:
                nb[side] = [p[:MAX_PARA] for p in b[side]]
        if "w" in b:
            nb["w"] = [[[t, s[:MAX_PARA]] for t, s in ops] for ops in b["w"]]
        out.append(nb)
    return out, dropped


def main():
    pairs = load("pairs.json")
    diffs = load("diffs.json")
    good = [d for d in diffs if not d.get("suspect") and d.get("revision") is not None]
    print("population %d   diffed %d   usable %d" % (len(pairs), len(diffs), len(good)))
    if not good:
        raise SystemExit("no usable diffs -- run src/diffs.py first")

    # ---- population, columnar -------------------------------------------------
    agencies = sorted({p["agency"] for p in pairs})
    ai = {a: i for i, a in enumerate(agencies)}
    pop = {
        "agency": [ai[p["agency"]] for p in pairs],
        "fdate": [p["final_date"] for p in pairs],
        "lag": [days(p["proposed_date"], p["final_date"]) for p in pairs],
        "window": [days(p["proposed_date"], p["comments_close_on"])
                   if p.get("comments_close_on") else None for p in pairs],
        "fpages": [p.get("final_pages") for p in pairs],
        "ppages": [p.get("proposed_pages") for p in pairs],
        "basis": [p["basis"] for p in pairs],
    }

    # ---- diffed rules ---------------------------------------------------------
    by_final = {p["final"]: p for p in pairs}
    rules = []
    for d in good:
        p = by_final.get(d["final"], {})
        rules.append({
            "f": d["final"], "p": d["proposed"],
            "t": d["title"], "ag": ai.get(d["agency"], -1),
            "fd": d["final_date"], "pd": d["proposed_date"],
            "rev": d["revision"], "sim": d["similarity"],
            "wf": d["words_final"], "wa": d["words_added"], "wr": d["words_removed"],
            "st": d["stratum"], "pb": d["posbins"],
            "cfr": p.get("cfr") or [],
            "fu": p.get("final_html_url"), "pu": p.get("proposed_html_url"),
            "win": days(p.get("proposed_date"), p.get("comments_close_on"))
                   if p.get("comments_close_on") else None,
        })
    rules.sort(key=lambda r: r["fd"])

    # ---- reading set ----------------------------------------------------------
    # Stratified across the revision range: a page that only lets you read the most
    # rewritten rules would make heavy revision look normal.
    rng = random.Random(SEED)
    bands, red = {}, {}
    for r in rules:
        b = min(9, int(r["rev"] * 10))
        bands.setdefault(b, []).append(r)
    per = max(1, READING_SET // max(1, len(bands)))
    chosen = []
    for b in sorted(bands):
        rows = bands[b]
        chosen.extend(rows if len(rows) <= per else rng.sample(rows, per))
    chosen = chosen[:READING_SET]
    dmap = {d["final"]: d for d in good}
    total_dropped = 0
    for r in chosen:
        blocks, dropped = trim_blocks(dmap[r["f"]]["blocks"])
        total_dropped += dropped
        red[r["f"]] = {"b": blocks, "more": dropped}
    print("reading set %d rules across %d revision bands (%d blocks elided)"
          % (len(red), len(bands), total_dropped))

    # ---- headline numbers, computed here so page and prose cannot drift --------
    rev = [r["rev"] for r in rules]
    sub = [r["rev"] for r in rules if r["st"] == "substantive"]
    ctl = [r["rev"] for r in rules if r["st"] == "control"]
    pos = [0] * 10
    for r in rules:
        tot = sum(r["pb"]) or 1
        for i, v in enumerate(r["pb"]):
            pos[i] += v / tot
    stats = {
        "pairs": len(pairs),
        "diffed": len(good),
        "suspect": len(diffs) - len(good),
        "median_rev": round(statistics.median(rev), 4),
        "median_sub": round(statistics.median(sub), 4) if sub else None,
        "median_ctl": round(statistics.median(ctl), 4) if ctl else None,
        "n_sub": len(sub), "n_ctl": len(ctl),
        "heavy": round(sum(1 for x in rev if x >= 0.25) / len(rev), 4),
        "heavy_ctl": round(sum(1 for x in ctl if x >= 0.25) / len(ctl), 4) if ctl else None,
        "light": round(sum(1 for x in rev if x < 0.05) / len(rev), 4),
        "median_lag": statistics.median([x for x in pop["lag"] if x is not None]),
        "median_window": statistics.median([x for x in pop["window"] if x is not None]),
        "posprofile": [round(x / len(rules), 5) for x in pos],
        "years": [min(pop["fdate"])[:4], max(pop["fdate"])[:4]],
    }
    print("median revision %.1f%%  (substantive %.1f%%, control %.1f%%)"
          % (100 * stats["median_rev"], 100 * (stats["median_sub"] or 0),
             100 * (stats["median_ctl"] or 0)))

    payload = {"generated": datetime.date.today().isoformat(), "agencies": agencies,
               "stats": stats, "pop": pop, "rules": rules, "red": red}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as g:
        g.write(raw)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    print("payload %.1f MB raw -> %.1f MB gzip -> %.1f MB base64"
          % (len(raw) / 1e6, buf.tell() / 1e6, len(b64) / 1e6))

    tpl = open(TEMPLATE, encoding="utf-8").read()
    if "__PAYLOAD__" not in tpl:
        raise SystemExit("template.html has no __PAYLOAD__ marker")
    if "<!DOCTYPE" not in tpl[:200].upper().replace("<!DOCTYPE", "<!DOCTYPE"):
        raise SystemExit("template.html is not a whole document -- refusing to publish a "
                         "fragment, which would render in quirks mode with UTF-8 read as "
                         "Latin-1. Supply the wrapper; never bypass this.")
    html = tpl.replace("__PAYLOAD__", b64)

    # Stage the page, prove it runs, and only then move it into place. A page that
    # parses is not a page that runs, and a script that dies partway still leaves the
    # static header rendering -- so a broken build looks fine in a screenshot and is
    # empty underneath. src/smoke.js executes the real script against the real payload.
    staged = OUT + ".staged"
    with open(staged, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    smoke = os.path.join(HERE, "smoke.js")
    if shutil.which("node") and os.path.exists(smoke):
        r = subprocess.run(["node", smoke, staged], capture_output=True, text=True)
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        print("   " + (out[-1] if out else "(smoke produced no output)"))
        if r.returncode != 0:
            os.remove(staged)
            raise SystemExit(
                "smoke test failed -- index.html left unchanged. Fix the page, or widen "
                "the shim in src/smoke.js if it lacks something the page legitimately "
                "uses. Do not delete the check.")
    else:
        print("   !! node not found -- page NOT smoke-tested")
    os.replace(staged, OUT)
    print("-> %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1e6))


if __name__ == "__main__":
    main()
