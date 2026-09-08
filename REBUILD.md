# Rebuilding Redline

For an LLM with a shell and no other context. This is a recipe, not a summary.

## What is being built

A single self-contained HTML page that answers one question with text rather than opinion:
**when the public comments on a proposed federal rule, does the rule change?**

Every final rule in the Federal Register is paired with the proposed rule it came from, and
the *regulatory text* of the two — the part that enters the Code of Federal Regulations, not
the preamble around it — is diffed word by word. The headline measure is **revision**: the
share of the words in the enacted text that were not in the proposal.

**The finding the page exists to show.** The median rule is enacted with about an eighth of
its regulatory text new since it was proposed, and that median is the least interesting number
on the page. The distribution is not a bell — it is a large mass of rules that barely move at
all and a long tail that moves a great deal. Roughly a quarter of diffed rules are at least a
quarter new; roughly a third barely move. An average over those two populations describes
neither of them.

The second finding is that agencies differ far more than the corpus median suggests — the FCC
sits above 50% median revision, the financial regulators (CFTC, SEC) in the mid-20s, EPA and
Energy near 18%, and Commerce and Agriculture near 5%. That is a tenfold spread and the most
interesting thing on the page after the shape of the distribution.

**Neither end of that chart is a scoreboard**, and the page says so. A low agency may publish
narrow, well-settled rules that were never going to move. A high one may be responsive — or
may just draft differently: an agency that reissues whole sections where another amends a
single paragraph measures as heavily revised on identical substance. The FCC is the clearest
suspect for that and is worth spot-checking through the redlines before anyone quotes its
number.

The control cohort settles that routine short rules revise too — around 5% median, with an
eighth of them still arriving at least a quarter new — so "rules change after comment" is not
an artefact of only reading long rules. They also revise distinctly *less* than substantive
ones (~12.5%).

**That gap only became trustworthy at scale, and the intermediate readings were misleading.**
At ~250 diffed rules the control sat slightly *above* the substantive median; at ~690 the two
were within half a point; at ~1,085 the control was a few points below; at ~3,700 it separated
cleanly. Anyone rebuilding this with a partial corpus will get a different answer and should
not narrate it. Note also that the strata are split on *page count*, so "substantive rules are
revised more" is close to a statement about length rather than about importance.

## Pipeline

Every script is stdlib-only Python 3, and each one writes a file the next one reads. Run in
order; all of them are re-runnable and the slow ones are resumable.

```bash
python3 src/harvest.py --from 2016 --to 2026   # ~55k documents of metadata      (~6 min)
python3 src/pair.py                            # -> data/pairs.json              (seconds)
python3 src/cohort.py                          # -> data/cohort.json             (seconds)
python3 src/fetch_text.py --parallel 8         # ~11.6k full texts, resumable   (~2.7 hours)
python3 src/diffs.py                           # -> data/diffs.json         (~1.5 hours)
python3 src/metrics.py                         # prints the numbers; writes nothing
python3 src/build_payload.py                   # -> ../index.html, gated on src/smoke.js
```

On Windows use `python`, not `python3` — see the workspace's PUBLISHING.md §0b.

## The data source

`https://www.federalregister.gov/api/v1/documents.json` — no key, no registration, honest
counts, and one of the better-designed government APIs. Its quirks, all of which cost time:

* **Always pass `fields[]`.** Without it every hit returns the full document object, which is
  large and mostly URLs.
* **Deep paging stops around 2,000 results** regardless of what `count` says. `harvest.py`
  slices by month and pages within the slice, which never approaches the ceiling.
* **`raw_text_url` ends in `.txt` and serves HTML** — `<html><head><title>…</title></head>
  <body><pre>` wrapped around the text. Read it as plain text and every document carries a
  header that is not part of the rule.
* **The text payload contains stray NUL bytes.** `grep` therefore calls the saved file binary
  and silently prints nothing, which reads exactly like "no matches" rather than an error.
  `fetch_text.py` strips them.
* **The host rate-limits full-text downloads and says so with 429 — check for it first.**
  This cost more time than every other quirk combined. A benchmark run before anything had
  been hammered suggested well over a thousand documents a minute; every measurement after it
  was an order of magnitude slower. That looked exactly like a client-side performance
  problem, and the fetcher went through two rewrites — thread pools, then connection pooling,
  then curl — before anyone read the status code. It had been 429 all along. Roughly **8
  concurrent transfers at ~60 documents a minute** is sustainable, and at that rate the whole
  cohort — 9,278 documents — came down in 161 minutes with **zero** requests throttled. Budget
  the time rather than trying to go faster; going faster is what produced the 429s.
* **Fetch order must be shuffled.** The cohort is date-sorted and a throttled run gets stopped
  part-way, so a prefix is every rule from 2016 to 2019 and none after. `fetch_text.py`
  shuffles with a fixed seed, which makes a partial fetch a random sample of the whole period
  and makes it reproducible.
* **On Windows, write forward slashes into curl's `-K` config file.** A backslash there is an
  escape character, so a native path is mangled into something curl cannot open — and with
  `--silent` it fails quietly, leaving a batch of empty files that look like genuine 404s.
  Four hundred documents were recorded as missing this way before anyone checked.

## Pairing, which is the hard part

There is no field that says "this final rule is the enactment of that proposal". Three keys
have to be combined, and **each one fails on its own in a way that produces confident nonsense
rather than an error**:

| key | how it fails alone |
| --- | --- |
| **RIN** | Some agencies run a *standing* RIN for a whole class of routine actions. `1625-AA00` — Coast Guard safety zones — carries 4,346 documents. Pairing on it marries unrelated rules by the thousand. Detected by volume (`CLASS_RIN_MAX`) and excluded from RIN matching. |
| **Docket** | Unique per action even inside a standing RIN, but an FCC or FAR docket is a *proceeding*: dozens of unrelated rules over years. It is also written a dozen ways (`Docket No. USCG-2026-1095`, `Docket Number …`), so normalisation lives in exactly one function, `norm_docket`. |
| **CFR part** | Both sides must amend the same part of the CFR. This alone rejected **8,582** candidate pairings that RIN and docket had accepted. |

Then two further checks run **against the text**, because the metadata is not sufficient:

* **The parts actually amended must overlap.** A long FCC or GSA rule lists every part in its
  neighbourhood in `cfr_references`, so two documents that amend completely different parts
  still show a metadata overlap. The `PART n--` headings inside the amendatory text are what a
  document really does.
* **Neither side may be more than four times the other.** Agencies publish companion documents
  — an order and a further notice, same day, adjacent document numbers — and the smaller one
  gets mistaken for the parent. This is what a 99% "revision" almost always turns out to be.

Pairs failing either check are kept in `diffs.json` with a `suspect` field and **excluded from
every number on the page**. Do not silently drop them; the count is worth reporting.

**Only about 40% of final rules pair at all**, and that is correct, not a bug. The rest are
direct final rules, corrections, rules issued without notice and comment, or rulemakings whose
proposal fell outside the harvest window. `C1-`/`C2-` prefixed documents are Federal Register
*corrections* and are dropped before pairing.

## Diffing, and five things that will silently ruin the numbers

1. **Unwrap before comparing.** Federal Register text is hard-wrapped at ~72 characters.
   Insert one word in a paragraph's first sentence and every following line re-wraps, so a
   line diff reports the whole paragraph as changed.

2. **Normalise the amendatory voice.** A proposal says *we propose to amend*, *is proposed to
   be revised*, *would be added*; the final says *we amend*, *is revised*, *is added*. Same
   instruction, different grammatical mood. Left alone, every rule in the corpus looks partly
   rewritten for reasons that have nothing to do with public comment. Nine substitutions,
   applied to both sides, listed in `NORMALISE` in `src/diffs.py`. Keep the list short and
   visible; it is the only text normalisation performed.

3. **Count revision at word level, not paragraph level.** This is the one that produced a
   wrong answer and looked plausible. Paragraph equality is exact-string equality, so a single
   changed cross-reference marks a whole paragraph as replaced. Counting every word in it as
   new put the median revision at **66%** — which would mean agencies rewrite two thirds of
   every rule after comment. They do not. Inside a replaced region the words are re-matched
   (`_word_tally`) and only genuinely new ones count. The median is about 12%.

The paragraph diff is kept for *display* — it is the unit a reader thinks in, and word-level
opcodes are only computed inside paragraphs that line up one-to-one, because an intra-word
diff of a wholesale rewrite is confetti.

Two more that cost time:

4. **`SequenceMatcher` is quadratic, and these documents are big enough for that to matter.**
   A single 30,000-word replaced region runs for minutes; a whole-corpus pass burned seventeen
   minutes of CPU without finishing and looked hung. Above `WORD_DIFF_MAX` combined words the
   tally falls back to **multiset overlap** — how many word occurrences the two sides share,
   ignoring order. That is an approximation, and deliberately a generous one for "kept", so
   revision on the very largest rewrites is if anything understated rather than inflated.

5. **Strip the page furniture.** `[[Page 51003]]` markers fall wherever the column break landed
   in the printed Federal Register, so they differ between two printings of otherwise identical
   text and register as real deletions. And re-join word tokens for display with something
   better than `" ".join` — otherwise `2. Section 510.2 is amended by--` renders as
   `2 . Section 510.2 is amended by - -`, which is the first thing a reader notices and makes
   an accurate diff look broken.

## Scope: two populations, never blurred

* **Population** — every paired rulemaking. Metadata only: comment window, time to final, page
  counts. Any claim at this scope covers the whole period.
* **Cohort** — the pairs whose text was fetched and diffed. Every claim about *words* comes
  from here, and it is a **stratified sample, not a census**: rules of ≥5 Federal Register
  pages in full, plus a fixed-seed random sample of shorter routine ones as a **control**. The
  control is not decoration — without it, "rules change after comment" could be an artefact of
  only reading long rules.

The page must keep these apart. It does, and so should any change to it.

## The page

`src/template.html` is the source; `index.html` is generated and should never be hand-edited.
The payload is gzipped, base64'd and inlined, and decompressed in the browser with
`DecompressionStream` — 7.2 MB of JSON ships as 1.6 MB of text.

**The build is gated on a smoke test.** `build_payload.py` writes `index.html.staged`, runs
`node src/smoke.js` against it, and only replaces `index.html` if the page actually rendered.
A page that parses is not a page that runs: a script that dies partway still leaves the static
header showing, so a broken build looks fine in a screenshot and is empty underneath. The shim
in `smoke.js` is deliberately dumb — enough DOM for this page and nothing more. When it is
missing something (`Option` and `classList` both had to be added), **widen the shim; never
delete the check.** It has been negative-tested against a deliberately hoisted `var` and fails
as it should.

Two more things worth keeping:

* **Remove the payload node after decoding.** A multi-megabyte base64 text node left in the
  document costs memory for the life of the page and measurably slows the renderer — Chrome's
  screenshot capture timed out on this page until `node.remove()` was added.
* **Only ~420 full redlines ship.** Shipping every diffed rule's changed paragraphs would be
  well over a hundred megabytes. The reading set is sampled *across the whole revision range*,
  not skimmed off the top — a page that only lets you read the most-rewritten rules would make
  heavy revision look normal. Every other rule links to the Federal Register's own text.

## What this does not measure

A diff says the text moved. It does not say **why**. Agencies revise in response to comments,
but also to internal review, litigation risk, OMB, and their own second thoughts. And revision
is a text measure, not a legal one: a one-word change to a threshold can matter more than a
thousand words of reorganisation, and this number cannot tell them apart. Say so on the page.
