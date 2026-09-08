#!/usr/bin/env python3
"""Corpus-level numbers, over the population and over the diffed cohort.

Two scopes, and the page must never blur them:

* **Population** -- all paired rulemakings 2016-2026. Metadata only: how long the
  comment window was, how long the agency then took, how the page count moved from
  proposal to final. Available for every pair, so any claim at this scope is about
  the whole period.
* **Cohort** -- the pairs whose full text was fetched and diffed. Everything about
  what the words actually did lives here, and it is a stratified sample, not a
  census: substantive rules in full, routine ones sampled. Comparing the two strata
  is the point -- it is what stops "rules get rewritten after comment" from being an
  artefact of only looking at long rules.

    python3 src/metrics.py
"""
import collections, datetime, json, os, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")


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


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(len(xs) * q / 100))]


def main():
    pairs = load("pairs.json")
    diffs = load("diffs.json")
    print("population %d pairs   cohort diffed %d" % (len(pairs), len(diffs)))

    # ---- population, metadata only -------------------------------------------
    lag = [days(p["proposed_date"], p["final_date"]) for p in pairs]
    window = [days(p["proposed_date"], p["comments_close_on"])
              for p in pairs if p.get("comments_close_on")]
    growth = [p["final_pages"] / p["proposed_pages"]
              for p in pairs if p.get("proposed_pages")]
    print("\nPOPULATION")
    print("  proposal -> final, days      p25 %s  median %s  p75 %s  p95 %s"
          % (pct(lag, 25), pct(lag, 50), pct(lag, 75), pct(lag, 95)))
    print("  comment window, days         p25 %s  median %s  p75 %s   (%d of %d state one)"
          % (pct(window, 25), pct(window, 50), pct(window, 75), len(window), len(pairs)))
    print("  final/proposed pages         p25 %.2f  median %.2f  p75 %.2f"
          % (pct(growth, 25), pct(growth, 50), pct(growth, 75)))

    if not diffs:
        print("\n(no diffs yet)")
        return

    # ---- cohort, text ---------------------------------------------------------
    print("\nCOHORT, by stratum")
    # A suspect pair is one the text-level checks in diffs.py distrusted. They are
    # kept in the file and counted here, but never averaged into anything.
    sus = [d for d in diffs if d.get("suspect")]
    diffs = [d for d in diffs if not d.get("suspect")]
    print("  %d diffs excluded as suspect; %d used" % (len(sus), len(diffs)))
    by = collections.defaultdict(list)
    for d in diffs:
        if d.get("revision") is not None:
            by[d["stratum"]].append(d)
    for stratum in ("substantive", "control"):
        rows = by.get(stratum) or []
        if not rows:
            continue
        rev = [r["revision"] for r in rows]
        unchanged = sum(1 for r in rows if r["revision"] == 0)
        heavy = sum(1 for r in rows if r["revision"] >= 0.25)
        print("  %-12s n=%-5d  median revision %5.1f%%   untouched %4.1f%%   "
              ">=25%% new %4.1f%%"
              % (stratum, len(rows), 100 * statistics.median(rev),
                 100.0 * unchanged / len(rows), 100.0 * heavy / len(rows)))

    allrows = [d for d in diffs if d.get("revision") is not None]
    rev = [d["revision"] for d in allrows]
    print("\n  revision distribution (share of enacted words absent from the proposal)")
    for q in (10, 25, 50, 75, 90, 99):
        print("    p%-3d %6.2f%%" % (q, 100 * pct(rev, q)))

    print("\n  by agency (>= 25 diffed rules), median revision")
    ag = collections.defaultdict(list)
    for d in allrows:
        ag[d["agency"]].append(d["revision"])
    rank = [(a, statistics.median(v), len(v)) for a, v in ag.items() if len(v) >= 25]
    rank.sort(key=lambda t: -t[1])
    for a, m, n in rank[:14]:
        print("    %-52s %5.1f%%  n=%d" % (a[:52], 100 * m, n))

    print("\n  most-rewritten rules in the cohort")
    top = sorted(allrows, key=lambda d: -d["revision"])[:8]
    for d in top:
        print("    %5.1f%%  %s  %s" % (100 * d["revision"], d["final_date"],
                                       (d["title"] or "")[:66]))

    print("\n  untouched: enacted exactly as proposed")
    zero = [d for d in allrows if d["revision"] == 0]
    print("    %d of %d (%.1f%%)" % (len(zero), len(allrows), 100.0 * len(zero) / len(allrows)))


if __name__ == "__main__":
    main()
