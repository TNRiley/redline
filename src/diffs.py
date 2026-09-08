#!/usr/bin/env python3
"""Diff each rulemaking's proposed regulatory text against what was enacted.

Three things have to be right or the numbers are noise.

**Unwrap first.** Federal Register text is hard-wrapped at ~72 characters. Insert one
word in the first sentence of a paragraph and every following line re-wraps, so a
line diff reports the whole paragraph as changed. Everything here is unwrapped to
logical paragraphs before anything is compared.

**Normalise the amendatory voice.** A proposal says "we propose to amend", "is
proposed to be revised", "would be added"; the final says "we amend", "is revised",
"is added". Those are the same instruction in two grammatical moods, and left alone
they make every single rule look partly rewritten. The substitutions are listed in
NORMALISE below -- deliberately few, deliberately visible, and applied to both sides.

**Diff twice, coarse then fine.** Paragraph-level first (cheap, and it is the unit a
reader thinks in), then word-level inside paragraphs that changed. A word-level diff
over a whole 40,000-token rule is both slow and unreadable.

    python3 src/diffs.py                 # build data/diffs.json
    python3 src/diffs.py --show 2016-00617
"""
import argparse, difflib, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
COHORT = os.path.join(HERE, "data", "cohort.json")
OUT = os.path.join(HERE, "data", "diffs.json")
sys.path.insert(0, HERE)
from extract import read, split          # noqa: E402

# Proposed-voice -> enacted-voice. Applied to both sides, so a final rule that
# happens to quote the proposal's phrasing is treated the same way.
NORMALISE = [
    (r"\bis proposed to be\b", "is"),
    (r"\bare proposed to be\b", "are"),
    (r"\bwe propose to\b", "we"),
    (r"\bwe are proposing to\b", "we"),
    (r"\bit is proposed to\b", "it is"),
    (r"\bproposed to read as follows\b", "to read as follows"),
    (r"\bwould be (revised|added|removed|amended|redesignated)\b", r"is \1"),
    (r"\bproposed (Sec\.|section|paragraph|part)\b", r"\1"),
    (r"\bthe proposed rule\b", "this rule"),
]
_NORM = [(re.compile(p, re.I), r) for p, r in NORMALISE]

# Typographic furniture from the printed Federal Register, not part of any rule.
# `[[Page 51003]]` markers fall wherever the column break happened to land, so they
# differ between two printings of identical text and register as real deletions.
_FURNITURE = re.compile(r"\[\[Page\s+[^\]]*\]\]")

_WS = re.compile(r"[ \t]+")
_WORD = re.compile(r"\w+(?:[-'.]\w+)*|[^\w\s]")
# A line that starts a new logical block rather than continuing the previous one.
_BLOCK = re.compile(
    r"^\s*(?:PART\s+\d|Subpart\b|Sec\.\s|§|\d+\.\s|\(\w{1,3}\)\s|"
    r"\*\s*\*\s*\*|Authority:|Source:|0\s+\d+\.)", re.I)

MAX_PARAS = 4000      # beyond this a rule is diffed at paragraph level only

_PART_HEAD = re.compile(r"^\s*PART\s+(\d+[A-Z]*)", re.M)
# A proposal's regulatory text this short is a stub -- usually the List of Subjects
# block picked up when the document amends nothing this extractor could find.
MIN_WORDS = 60


def amended_parts(regtext):
    """The CFR parts a document's amendatory text actually touches.

    The metadata's `cfr_references` is not enough on its own. A long FCC or GSA rule
    lists every part in its neighbourhood, so two documents that amend completely
    different parts still show an overlap there and pass the metadata check in
    pair.py. The PART headings inside the amendatory text are what the document
    really does, and comparing those catches the pairs that survive the first test.
    """
    return set(_PART_HEAD.findall(regtext or ""))


def normalise(s):
    s = _FURNITURE.sub(" ", s)
    for rx, rep in _NORM:
        s = rx.sub(rep, s)
    return s


# Tokens are re-joined for display, not re-parsed, so spacing only has to look right.
_NO_SPACE_BEFORE = set(".,;:!?)]}%")
_NO_SPACE_AFTER = set("([{$")


def detokenize(toks):
    """Join word tokens back into readable prose.

    A naive " ".join turns "2. Section 510.2 is amended by--" into "2 . Section 510.2
    is amended by - -", which makes every redline look mangled and is the first thing
    a reader notices. The Federal Register also writes quotes as `` and '' , which
    render as stray backticks unless they are folded back to real quotation marks.
    """
    out = []
    for t in toks:
        if out and (t[0] in _NO_SPACE_BEFORE or out[-1][-1] in _NO_SPACE_AFTER):
            out[-1] += t
        else:
            out.append(t)
    s = " ".join(out)
    s = s.replace("` `", "“").replace("`` ", "“").replace("``", "“")
    s = s.replace("' '", "”").replace("'' ", "”").replace("''", "”")
    return re.sub(r"\s+", " ", s).strip()


def paragraphs(text):
    """Unwrap hard-wrapped Federal Register text into logical paragraphs."""
    out, buf = [], []
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        # A structural line begins a paragraph even without a blank line before it.
        if _BLOCK.match(line) and buf:
            out.append(" ".join(buf))
            buf = []
        buf.append(stripped)
    if buf:
        out.append(" ".join(buf))
    return [_WS.sub(" ", p).strip() for p in out if p.strip()]


def words(s):
    return _WORD.findall(s)


def word_ops(a, b):
    """Word-level opcodes inside one changed paragraph, as compact pairs."""
    wa, wb = words(a), words(b)
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(["=", detokenize(wa[i1:i2])])
        elif tag == "delete":
            ops.append(["-", detokenize(wa[i1:i2])])
        elif tag == "insert":
            ops.append(["+", detokenize(wb[j1:j2])])
        else:
            ops.append(["-", detokenize(wa[i1:i2])])
            ops.append(["+", detokenize(wb[j1:j2])])
    return ops


def _word_tally(a_paras, b_paras):
    """Words kept / added / removed across a changed region, at word level.

    This has to be word level or the headline number is wrong. Paragraph equality is
    exact-string equality, so a single changed cross-reference marks the whole
    paragraph as replaced -- and counting every word in it as new put the median
    "revision" at 66%, which would mean agencies rewrite two thirds of every rule
    after comment. They do not. Inside a replaced region the words are re-matched
    here, and only genuinely new ones count as added.
    """
    wa, wb = words(" ".join(a_paras)), words(" ".join(b_paras))
    # SequenceMatcher is quadratic in the worst case; past this size the region is a
    # wholesale rewrite anyway and the exact split does not change the story.
    if len(wa) + len(wb) > 60000:
        return 0, len(wb), len(wa)
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    kept = sum(n for _, _, n in sm.get_matching_blocks())
    return kept, len(wb) - kept, len(wa) - kept


def diff_pair(prop_text, final_text, want_ops=True):
    pa, pb = paragraphs(normalise(prop_text)), paragraphs(normalise(final_text))
    sm = difflib.SequenceMatcher(None, pa, pb, autojunk=False)

    kept = added = removed = 0
    w_kept = w_added = w_removed = 0
    blocks = []
    heavy = len(pa) + len(pb) > MAX_PARAS

    # Where in the enacted rule the new words land, in ten bins over the final
    # document. Changes are not spread evenly -- a rule that grew a new exemption
    # grew it somewhere in particular -- and this is the only cheap way to see that
    # at corpus scale. Only genuinely new words are binned, so a lightly edited
    # paragraph does not register as wholly new.
    posbins = [0] * 10
    total_b = sum(len(words(p)) for p in pb) or 1
    seen_b = 0

    def _bin(new_words, at):
        if new_words:
            posbins[min(9, int(10 * at / total_b))] += new_words

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            kept += i2 - i1
            n = sum(len(words(p)) for p in pb[j1:j2])
            w_kept += n
            seen_b += n
            blocks.append({"t": "=", "n": i2 - i1})
        elif tag == "delete":
            removed += i2 - i1
            w_removed += sum(len(words(p)) for p in pa[i1:i2])
            blocks.append({"t": "-", "a": pa[i1:i2]})
        elif tag == "insert":
            added += j2 - j1
            n = sum(len(words(p)) for p in pb[j1:j2])
            w_added += n
            _bin(n, seen_b)
            seen_b += n
            blocks.append({"t": "+", "b": pb[j1:j2]})
        else:
            removed += i2 - i1
            added += j2 - j1
            k, ad, rm = _word_tally(pa[i1:i2], pb[j1:j2])
            w_kept += k
            w_added += ad
            w_removed += rm
            _bin(ad, seen_b)
            seen_b += k + ad
            blk = {"t": "~", "a": pa[i1:i2], "b": pb[j1:j2]}
            # Word-level detail only where the two sides line up one-to-one; a
            # many-to-many replacement is a rewrite, and an intra-word diff of a
            # rewrite is confetti.
            if want_ops and not heavy and (i2 - i1) == (j2 - j1) and (i2 - i1) <= 12:
                blk["w"] = [word_ops(pa[i], pb[j])
                            for i, j in zip(range(i1, i2), range(j1, j2))]
            blocks.append(blk)

    w_final = w_kept + w_added
    return {
        "posbins": posbins,
        "paras_proposed": len(pa), "paras_final": len(pb),
        "paras_kept": kept, "paras_added": added, "paras_removed": removed,
        "words_final": w_final, "words_added": w_added, "words_removed": w_removed,
        "similarity": round(sm.ratio(), 4),
        # The headline number: how much of the enacted text was not in the proposal.
        "revision": round(w_added / w_final, 4) if w_final else None,
        "blocks": blocks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    cohort = json.load(open(COHORT, encoding="utf-8"))

    if a.show:
        for p in cohort:
            if a.show in (p["final"], p["proposed"]):
                d = diff_pair(split(read(p["proposed"]) or "")["regtext"],
                              split(read(p["final"]) or "")["regtext"])
                d.pop("blocks")
                print(json.dumps({"title": p["title"], **d}, indent=2))
                return
        print("not in the cohort: %s" % a.show)
        return

    todo = cohort[:a.limit] if a.limit else cohort
    out, skipped, suspect, t0 = [], 0, 0, time.time()
    for i, p in enumerate(todo, 1):
        ft, pt = read(p["final"]), read(p["proposed"])
        if not ft or not pt:
            skipped += 1
            continue
        fr, pr = split(ft)["regtext"], split(pt)["regtext"]
        if not fr or not pr:
            skipped += 1
            continue

        # Two text-level checks that the metadata cannot make. Both mark the pair
        # rather than dropping it, so the page can report how many were excluded and
        # why instead of quietly showing a smaller corpus.
        fp, pp = amended_parts(fr), amended_parts(pr)
        reasons = []
        if fp and pp and not (fp & pp):
            reasons.append("amends different CFR parts")
        wp, wf = len(words(pr)), len(words(fr))
        if wp < MIN_WORDS or wf < MIN_WORDS:
            reasons.append("one side has no usable amendatory text")
        # A final whose amendatory text dwarfs its proposal's is usually not that
        # proposal grown up. Agencies publish companion documents -- an order and a
        # further notice on the same day, adjacent document numbers -- and the
        # smaller one gets picked as the parent. Genuine wholesale expansion after
        # comment exists but is rare, and both look identical from the metadata.
        elif max(wp, wf) > 4 * min(wp, wf):
            reasons.append("one side is more than four times the other")
        if reasons:
            suspect += 1

        d = diff_pair(pr, fr)
        d.update({"suspect": reasons or None,
                  "final": p["final"], "proposed": p["proposed"],
                  "stratum": p["stratum"], "agency": p["agency"],
                  "title": p["title"], "final_date": p["final_date"],
                  "proposed_date": p["proposed_date"]})
        out.append(d)
        if i % 100 == 0:
            el = time.time() - t0
            sys.stderr.write("\r  %d/%d  diffed %d  skipped %d  %.0f/s  "
                             % (i, len(todo), len(out), skipped, i / max(el, 1e-9)))
            sys.stderr.flush()
    sys.stderr.write("\n")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("diffed %d pairs, skipped %d (no text or no regulatory text on one side)"
          % (len(out), skipped))
    print("  of those, %d flagged suspect and excluded from headline numbers" % suspect)
    print("-> %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1e6))


if __name__ == "__main__":
    main()
