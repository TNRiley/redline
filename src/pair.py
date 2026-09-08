#!/usr/bin/env python3
"""Pair each final rule with the proposed rule it came from.

Two join keys, because neither works alone:

* **RIN** is the Unified Agenda's rulemaking id and is usually exactly right --
  1545-BR75 carries one proposal and one final. But some agencies run a *standing*
  RIN for a whole class of routine actions: 1625-AA00 (Coast Guard safety zones)
  carries 4,346 documents, 2120-AA64 (FAA airworthiness directives) thousands more.
  Pairing on those alone would marry unrelated rules by the thousand.
* **Docket** is unique per action even inside a standing RIN (USCG-2026-1094), but is
  absent on many documents, and the two sides of an IRS rulemaking use different
  docket schemes entirely (REG-118269-23 proposing, TD 10054 finalising).

So: a RIN carrying more than CLASS_RIN_MAX finals is a class RIN and may only pair
through a shared docket. A specific RIN pairs on the RIN itself. Every pair records
which key carried it, because a docket match is a fact and a class-RIN match is not
available at all.

    python3 src/pair.py
    python3 src/pair.py --explain 2026-18219
"""
import argparse, collections, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "data", "docs")
OUT = os.path.join(HERE, "data", "pairs.json")

# A RIN with more finals than this is a standing class RIN, not one rulemaking.
# The real distribution is bimodal with a wide empty gap: ordinary rulemakings sit at
# 1-3 finals, class RINs in the hundreds. Anything in between is safer treated as a
# class RIN, since a wrong pair produces a confident, meaningless diff.
CLASS_RIN_MAX = 12

_DOCKET_NOISE = re.compile(
    r"^(docket\s*(no\.?|number|id)?|notice\s*no\.?|reg\b)[\s:.\-]*", re.I)
_NONALNUM = re.compile(r"[^A-Z0-9]+")


def norm_docket(s):
    """One normaliser, used everywhere. Agencies write the same docket a dozen ways:
    'Docket No. USCG-2026-1095', 'Docket Number USCG-2026-1095', 'USCG 2026 1095'."""
    if not s:
        return ""
    s = _DOCKET_NOISE.sub("", str(s).strip())
    return _NONALNUM.sub("-", s.upper()).strip("-")


def cfr_parts(doc):
    """The (title, part) pairs a document amends. Present on essentially every
    document, and the only field that says what a rule is actually about."""
    out = set()
    for r in (doc.get("cfr_references") or []):
        t, p = (r or {}).get("title"), (r or {}).get("part")
        if t is not None and p is not None:
            out.add((t, p))
    return out


def agency_of(doc):
    ags = doc.get("agencies") or []
    for a in ags:
        n = (a or {}).get("name") or (a or {}).get("raw_name")
        if n:
            return n
    return "Unknown"


# C1-/C2- prefixed documents are Federal Register *corrections* to an already
# published document -- typically a page of errata. They are not rulemakings and
# pairing one produces a confident diff of a rule against a typo fix.
_CORRECTION = re.compile(r"^C\d+-")


def load():
    rows = []
    for f in sorted(os.listdir(DOCS)):
        if f.endswith(".json"):
            rows.extend(json.load(open(os.path.join(DOCS, f), encoding="utf-8")))
    # The API can serve a document in two month slices at a boundary; de-dupe.
    seen, out = set(), []
    for r in rows:
        dn = r.get("document_number")
        if dn and dn not in seen and not _CORRECTION.match(dn):
            seen.add(dn)
            out.append(r)
    return out


def build(rows):
    finals = [r for r in rows if r.get("type") == "Rule"]
    props = [r for r in rows if r.get("type") == "Proposed Rule"]

    rin_finals = collections.Counter()
    for r in finals:
        for rin in (r.get("regulation_id_numbers") or []):
            rin_finals[rin] += 1
    class_rins = {r for r, n in rin_finals.items() if n > CLASS_RIN_MAX}

    by_rin, by_docket = collections.defaultdict(list), collections.defaultdict(list)
    for p in props:
        for rin in (p.get("regulation_id_numbers") or []):
            by_rin[rin].append(p)
        for d in (p.get("docket_ids") or []):
            nd = norm_docket(d)
            if nd:
                by_docket[nd].append(p)

    pairs, stats = [], collections.Counter()
    for f in finals:
        f_rins = [r for r in (f.get("regulation_id_numbers") or [])]
        f_dockets = [norm_docket(d) for d in (f.get("docket_ids") or []) if norm_docket(d)]

        cands = {}
        for d in f_dockets:
            for p in by_docket.get(d, []):
                cands.setdefault(p["document_number"], (p, set()))[1].add("docket")
        for rin in f_rins:
            if rin in class_rins:
                continue
            for p in by_rin.get(rin, []):
                cands.setdefault(p["document_number"], (p, set()))[1].add("rin")

        # A proposal must precede the final it became, and must amend the same corner
        # of the CFR. That second test is not a nicety: an FCC or FAR docket is a
        # *proceeding*, not a rulemaking, and carries dozens of unrelated rules over
        # years -- the same failure mode as a class RIN, one level down. Without it a
        # proposal amending 47 CFR 65 pairs with a final amending 47 CFR 51 and the
        # diff reports a 99% rewrite, confidently and wrongly.
        f_cfr = cfr_parts(f)
        viable = []
        for p, k in cands.values():
            if p.get("publication_date", "") >= f.get("publication_date", ""):
                continue
            if f_cfr and cfr_parts(p) and not (f_cfr & cfr_parts(p)):
                stats["rejected_cfr_mismatch"] += 1
                continue
            viable.append((p, k))
        if not viable:
            stats["unpaired"] += 1
            if f_rins and all(r in class_rins for r in f_rins) and not f_dockets:
                stats["unpaired_class_rin_no_docket"] += 1
            continue

        # The nearest preceding proposal is the one this final responds to; an earlier
        # one in the same docket is a superseded round, not this rule's parent.
        viable.sort(key=lambda pk: pk[0].get("publication_date", ""))
        p, keys = viable[-1]
        basis = "docket+rin" if keys == {"docket", "rin"} else ("docket" if "docket" in keys else "rin")
        stats["paired"] += 1
        stats["basis_" + basis] += 1
        pairs.append({
            "final": f["document_number"],
            "proposed": p["document_number"],
            "basis": basis,
            "rounds": len(viable),
            "title": f.get("title"),
            "agency": agency_of(f),
            "rin": f_rins[0] if f_rins else None,
            "final_date": f.get("publication_date"),
            "proposed_date": p.get("publication_date"),
            "comments_close_on": p.get("comments_close_on"),
            "final_pages": f.get("page_length"),
            "proposed_pages": p.get("page_length"),
            "final_text_url": f.get("raw_text_url"),
            "proposed_text_url": p.get("raw_text_url"),
            "final_html_url": f.get("html_url"),
            "proposed_html_url": p.get("html_url"),
            "cfr": sorted("%s CFR %s" % (t, pt) for t, pt in cfr_parts(f)),
        })
    return pairs, stats, class_rins, finals, props


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain")
    a = ap.parse_args()
    rows = load()
    pairs, stats, class_rins, finals, props = build(rows)

    if a.explain:
        for p in pairs:
            if a.explain in (p["final"], p["proposed"]):
                print(json.dumps(p, indent=2, ensure_ascii=False))
                return
        print("no pair contains %s" % a.explain)
        return

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(pairs, f, ensure_ascii=False)

    print("%d documents  (%d final, %d proposed)" % (len(rows), len(finals), len(props)))
    print("%d class RINs excluded from RIN pairing" % len(class_rins))
    print("paired   %d  (%.1f%% of finals)" % (stats["paired"], 100.0 * stats["paired"] / max(1, len(finals))))
    for k in ("basis_docket+rin", "basis_docket", "basis_rin"):
        if stats[k]:
            print("   %-18s %d" % (k[6:], stats[k]))
    print("unpaired %d  (%d of them class-RIN with no docket)"
          % (stats["unpaired"], stats["unpaired_class_rin_no_docket"]))
    print("candidates rejected for amending a different part of the CFR: %d"
          % stats["rejected_cfr_mismatch"])
    print("-> %s" % OUT)


if __name__ == "__main__":
    main()
