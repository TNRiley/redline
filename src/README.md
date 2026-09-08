# The pipeline

Stdlib-only Python 3, no dependencies except `curl` for the download step. Each script
writes a file the next one reads, all are re-runnable, and the slow ones resume.

```bash
python3 harvest.py --from 2016 --to 2026   # Federal Register metadata -> data/docs/<year>.json
python3 pair.py                            # match final rules to their proposals -> data/pairs.json
python3 cohort.py                          # choose what gets full text -> data/cohort.json
python3 fetch_text.py --parallel 8         # full text -> data/text/<document>.txt  (rate-limited)
python3 diffs.py                           # word-level diffs -> data/diffs.json
python3 metrics.py                         # print the corpus numbers; writes nothing
python3 build_payload.py                   # splice payload into ../index.html
```

`extract.py` is a library, not a step: it splits one Federal Register document into header,
summary, preamble, regulatory text, and the section where a final rule states what it changed.
`python3 extract.py --show <document-number>` prints those parts for one document, and
`--audit N` reports how many of the first N pairs have each of them.

| file | what it is |
| --- | --- |
| `harvest.py` | month-sliced metadata harvest; the API's deep paging stops around 2,000 results |
| `pair.py` | the record linkage — RIN, docket and CFR part, none of which works alone |
| `cohort.py` | picks the substantive rules plus a seeded random control cohort |
| `fetch_text.py` | curl-driven, resumable, shuffled, and polite about the 429s |
| `extract.py` | document → parts; the `List of Subjects` boundary is what matters |
| `diffs.py` | unwrap, normalise the amendatory voice, diff paragraphs then words |
| `metrics.py` | the numbers, kept out of the page so they can be checked independently |
| `build_payload.py` | columnar gzip payload + the reading set, spliced into the template |
| `template.html` | the page. **Edit here — `../index.html` is generated.** |

Everything under `data/` is generated and gitignored. `../REBUILD.md` is the full account,
including the traps that cost time and the reasoning behind the pairing rules; read it before
changing anything in `pair.py` or `diffs.py`, because both have failure modes that produce
confident, plausible, wrong numbers rather than errors.
