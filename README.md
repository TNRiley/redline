# ✒️ Redline

**Every federal rule, diffed against the version its agency first proposed — what public comment actually changed.**

→ **[Open it](https://tnriley.github.io/redline/)**

American rulemaking promises that the public gets to comment and the agency may change its mind. Whether that second step moves the text is an empirical question nobody answers, because answering it means diffing two documents for every rule. This pairs 13,626 final rules from the Federal Register with the proposals they came from and compares the regulatory text word by word — the part that enters the Code of Federal Regulations, not the preamble around it. The median rule is enacted with an eighth of its regulatory text new since it was proposed, and that median is the least interesting number on the page: the distribution is not a bell but a third of rules barely moving and a quarter arriving at least a quarter new. Agencies differ more than any of that suggests, from Energy at 28% median revision to Agriculture at 4%. Pairing is the hard part and no field does it — a RIN can be a standing class covering 4,346 documents, an FCC docket is a proceeding spanning years, and requiring CFR-part agreement threw out 8,582 candidate pairings that the other keys accepted. A routine-rule control cohort runs throughout, and the page is explicit that the gap between the strata has not been stable and should not be read as a result.

## Running it

One self-contained HTML file. No build step, no server, no network access at runtime — open `index.html` in a browser, or serve the directory with any static host.

```bash
python3 -m http.server 8000   # then visit http://localhost:8000
```

## Rebuilding it from scratch

[REBUILD.md](REBUILD.md) is written for an LLM with a shell and nothing else: the data sources and their quirks, the processing decisions, the page's structure and interactions, and a table of expected values to check the result against.

## Source

The full build pipeline is in [`src/`](src/), with a README describing how to regenerate the page from scratch.

## Data

- **[Federal Register API (documents, metadata and full text), US National Archives / GPO](https://www.federalregister.gov/developers/documentation/api/v1)** — US Government work — public domain; the full-text endpoint rate-limits and answers 429

Every figure on the page is computed from the data shipped with it. Check the page's own methods panel for how each number is derived and where it should not be pushed.

## Built with

python 3 stdlib, curl for the rate-limited harvest, vanilla JS, gzip + DecompressionStream payload, canvas, word-level diff over unwrapped paragraphs.

## Licence

Code is MIT (see [LICENSE](LICENSE)). Data keeps the licence of its source, listed above.

---

Part of [Quick Projects](https://github.com/TNRiley/quick-projects) — one self-contained thing, built in one session. First published 2026-09-07.
