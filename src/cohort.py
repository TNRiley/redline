#!/usr/bin/env python3
"""Choose which pairs get their full text fetched, and record why.

15,174 pairs is more full text than this build can responsibly pull, and most of the
tail is not worth a diff: a one-page amendment moving a Class E airspace boundary is
a real rulemaking but not an interesting one. So the corpus is explicitly two-tier,
and the page says so:

* **Every pair** gets the metadata analysis -- lag, comment window, page growth.
  Nothing is dropped from that; it is the whole 2016-2026 population.
* **A text cohort** gets the proposed-vs-final diff. It is the substantive rules
  (>= PAGE_MIN Federal Register pages in the final) plus a random sample of the
  routine ones, carried as a **control**: without it, any claim that "rules change
  after comment" would be a claim about long rules only.

The control is drawn with a fixed seed so the cohort is reproducible.

    python3 src/cohort.py
"""
import argparse, collections, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
PAIRS = os.path.join(HERE, "data", "pairs.json")
OUT = os.path.join(HERE, "data", "cohort.json")

PAGE_MIN = 5          # FR pages in the final rule
CONTROL_N = 900       # routine pairs sampled as a control cohort
SEED = 20260907


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-min", type=int, default=PAGE_MIN)
    ap.add_argument("--control", type=int, default=CONTROL_N)
    a = ap.parse_args()

    pairs = json.load(open(PAIRS, encoding="utf-8"))
    subst = [p for p in pairs if (p.get("final_pages") or 0) >= a.page_min]
    routine = [p for p in pairs if (p.get("final_pages") or 0) < a.page_min]

    rng = random.Random(SEED)
    control = rng.sample(routine, min(a.control, len(routine)))

    for p in subst:
        p["stratum"] = "substantive"
    for p in control:
        p["stratum"] = "control"

    cohort = subst + control
    cohort.sort(key=lambda p: p["final_date"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cohort, f, ensure_ascii=False)

    print("population   %d pairs  (metadata analysis covers all of these)" % len(pairs))
    print("text cohort  %d pairs  = %d substantive (>=%d pages) + %d control of %d routine"
          % (len(cohort), len(subst), a.page_min, len(control), len(routine)))
    print("documents to fetch: %d" % (2 * len(cohort)))
    by = collections.Counter(p["agency"] for p in cohort)
    print("\ntop agencies in the cohort:")
    for ag, n in by.most_common(8):
        print("  %-50s %d" % (ag[:50], n))
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
