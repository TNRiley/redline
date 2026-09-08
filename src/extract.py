#!/usr/bin/env python3
"""Split a Federal Register document into its parts.

Every FR rule has the same skeleton, and the boundary that matters is `List of
Subjects`: everything before it is the **preamble** (the agency explaining itself,
including its answers to comments), everything after is the **regulatory text** --
the amendments that actually enter the CFR. Diffing the whole document would mostly
compare two essays; diffing after this split compares two versions of the law.

    header      the FR citation block, agency, CFR parts, RIN, ACTION
    summary     the SUMMARY: paragraph
    preamble    SUPPLEMENTARY INFORMATION .. List of Subjects
    regtext     the first `PART n--` at or after List of Subjects .. `[FR Doc.`
    changes     the preamble section where a final rule says what it changed, if any

Documents with no regulatory text exist and are not a failure: a notice of hearing,
or a final rule that only confirms an earlier effective date, amends nothing.

    python3 src/extract.py --show 2026-18219
"""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT = os.path.join(HERE, "data", "text")

_SUMMARY = re.compile(r"^SUMMARY:\s*", re.M)
_SUPP = re.compile(r"^SUPPLEMENTARY INFORMATION:?\s*$|^SUPPLEMENTARY INFORMATION:", re.M)
_LOS = re.compile(r"^List of Subjects\b", re.M)
_PART = re.compile(r"^\s*PART\s+\d+[A-Z]*--", re.M)
_TRAILER = re.compile(r"^\s*\[FR Doc\.", re.M)
_DATES = re.compile(r"^DATES:", re.M)

# What a final rule calls the section where it admits what the comments changed.
# Ordered: the most specific heading wins when a document carries several.
_CHANGE_HEADINGS = [
    r"Changes? (?:from|to|Made to) the (?:Proposed Rule|NPRM|Proposal)[^\n]*",
    r"Differences? Between the Proposed(?: Rule)? and (?:This|the) Final Rule[^\n]*",
    r"Summary of (?:the )?(?:Comments|Changes)[^\n]*",
    r"(?:Public )?Comments? and (?:Responses?|Our Responses?)[^\n]*",
    r"Response(?:s)? to (?:Public )?Comments[^\n]*",
    r"Explanation of (?:Revisions|Changes)[^\n]*",
]
_CHANGE_RE = re.compile(
    r"^[ \t]*(?:[IVXAB0-9]+\.\s*)?(" + "|".join(_CHANGE_HEADINGS) + r")[ \t]*$",
    re.M | re.I)


def read(dn):
    p = os.path.join(TEXT, dn + ".txt")
    if not os.path.exists(p):
        return None
    s = open(p, encoding="utf-8").read()
    return s or None


def split(s):
    """Return a dict of the parts. Missing parts are empty strings, never None,
    so callers can treat them uniformly."""
    out = {"header": "", "summary": "", "preamble": "", "regtext": "", "changes": ""}
    if not s:
        return out

    end = _TRAILER.search(s)
    body = s[:end.start()] if end else s

    m_sum = _SUMMARY.search(body)
    out["header"] = body[:m_sum.start()] if m_sum else body[:2000]
    if m_sum:
        m_dates = _DATES.search(body, m_sum.end())
        out["summary"] = body[m_sum.end():m_dates.start() if m_dates else m_sum.end() + 3000].strip()

    m_los = _LOS.search(body)
    m_supp = _SUPP.search(body)
    pre_start = m_supp.end() if m_supp else (m_sum.end() if m_sum else 0)
    pre_end = m_los.start() if m_los else len(body)
    if pre_end > pre_start:
        out["preamble"] = body[pre_start:pre_end].strip()

    # Regulatory text: the first PART heading at or after List of Subjects. Searching
    # from the top instead would catch a PART named in the preamble's discussion.
    search_from = m_los.start() if m_los else pre_start
    m_part = _PART.search(body, search_from)
    if m_part:
        out["regtext"] = body[m_part.start():].strip()
    elif m_los:
        # Some agencies amend without a PART heading (a single section revision).
        tail = body[m_los.start():].strip()
        out["regtext"] = tail if len(tail) > 400 else ""

    if out["preamble"]:
        m_ch = _CHANGE_RE.search(out["preamble"])
        if m_ch:
            out["changes"] = out["preamble"][m_ch.start():m_ch.start() + 20000].strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--audit", type=int, help="report coverage over N documents")
    a = ap.parse_args()

    if a.show:
        d = split(read(a.show))
        for k in ("header", "summary", "preamble", "regtext", "changes"):
            v = d[k]
            print("=== %s (%d chars) ===" % (k, len(v)))
            print(v[:600].replace("\n", " ")[:600])
            print()
        return

    if a.audit:
        import json
        pairs = json.load(open(os.path.join(HERE, "data", "pairs.json"), encoding="utf-8"))
        n = has_reg = has_pre = has_ch = missing = 0
        for p in pairs[:a.audit]:
            for side in ("final", "proposed"):
                s = read(p[side])
                if s is None:
                    missing += 1
                    continue
                d = split(s)
                n += 1
                has_reg += bool(d["regtext"])
                has_pre += bool(d["preamble"])
                if side == "final":
                    has_ch += bool(d["changes"])
        print("documents read      %d  (%d not on disk)" % (n, missing))
        print("with preamble       %d  (%.0f%%)" % (has_pre, 100.0 * has_pre / max(1, n)))
        print("with regulatory text %d  (%.0f%%)" % (has_reg, 100.0 * has_reg / max(1, n)))
        print("finals with a stated-changes section %d" % has_ch)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
