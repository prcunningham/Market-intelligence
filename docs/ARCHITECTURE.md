# Architecture

## Layering

```
sources/openfda/        thin REST client + per-endpoint field metadata
        |
segments.py             segment definition -> per-endpoint openFDA query
        |
pipeline.py              fetch -> normalize -> persist orchestration
        |               \
        |                normalize.py   company alias + category lookup
        |
db.py                   SQLite: raw_records, segment_records, events,
        |                        companies, segments, fetch_log
        |
analysis.py              events -> pivots / new-entrants / top-movers / trends
        |
cli.py                   argparse commands over the above
```

Each layer only depends on the interfaces of the layer below it, not its
internals, so a future source (FCC, USPTO, regs.gov, CMS,
ClinicalTrials.gov) can plug into `segments.py`, `normalize.py`,
`db.py`, and `analysis.py` the same way openFDA does, per source-specific
client module under `sources/<name>/`.

## Why two tiers of storage (`raw_records` vs `events`)

`raw_records` is the source of truth: one row per (endpoint, record_id),
storing the exact JSON openFDA returned. `events` is a derived,
denormalized fact table (one row per endpoint/record/product-code
combination, with normalized company name, category, and year already
computed) that `analysis.py` queries directly.

The split matters because normalization rules (company aliases, category
tags) are expected to change often as you use this tool, while raw API
data does not need to be re-fetched just because you added an alias. Run
`python -m market_intel rebuild-events` to recompute `events` from the
cached `raw_records` after editing `config/company_aliases.yaml` or
`config/company_categories.yaml` — no API calls involved.

`segment_records` is a link table between segment names and
(endpoint, record_id) pairs. This lets two overlapping segments (e.g. a
broad "respiratory devices" segment and a narrower "CPAP masks" segment)
share the same cached raw records without duplicating storage, and lets a
segment's product-code/date/applicant filters change over time — a refresh
clears and rebuilds only that segment's links, never the underlying raw
data another segment might still depend on.

## Segment model

A segment (`segments.py`) is a small, serializable filter specification —
product codes, CFR regulation numbers, device classes, applicant names, a
date range, and/or a free-text openFDA query fragment — plus the list of
device endpoints it applies to. `Segment.query_for_endpoint()` compiles
that specification into the specific openFDA `search=` query string for
one endpoint, accounting for the fact that different endpoints expose the
same concept (company name, product code, device class) under different
field paths (see `sources/openfda/endpoints.py`'s `EndpointSpec`).

This is deliberately more general than the single hardcoded product-code
basket from the earlier manual sleep-appliance project: any future
engagement defines a new segment by adding a YAML file under
`config/segments/`, not by writing code.

## Company normalization vs. category tagging

These are two independent, hand-maintained YAML-driven lookups
(`normalize.py`):

- **Alias -> canonical parent** (`company_aliases.yaml`): collapses
  subsidiary/historical spellings (e.g. "Respironics" or "Philips RS North
  America") to a current parent name ("Philips"). Matching is
  case/punctuation/legal-suffix insensitive. Names with no alias entry are
  left as a cleaned, title-cased version of themselves — they are not
  forced into a mapping that doesn't exist, which matters for the many
  small/individual 510(k) applicants that really are independent.
- **Canonical -> category tag** (`company_categories.yaml`): a free-form
  taxonomy (Strategic/Incumbent Medtech, PE-Backed Platform, Single-Product
  Startup, CDMO/Private-Label Filer, or whatever an engagement needs).
  Unmapped companies are tagged `Unclassified` rather than guessed.

Both are plain YAML edited directly — there is no UI or code change
required to add a mapping or a category, by design, since this taxonomy
is inherently an analyst judgment call that will keep evolving.

## Unified entity model (future work)

The end state described in the project brief is one normalized "company"
dimension that six sources map into: FDA applicant, FCC grantee, USPTO
assignee, regulations.gov commenter/submitter, CMS provider/manufacturer,
and ClinicalTrials.gov sponsor. This platform's `companies` table and
`normalize.py` alias table are that dimension's starting point, but they
currently only ingest FDA applicant/manufacturer/firm names.

Do not build pairwise joins between each new source and openFDA as later
phases land. Instead, each new source should feed the *same*
`company_aliases.yaml`/`companies` table (adding aliases for its own
naming convention — an FCC grantee name, a PatentsView assignee name, a
regulations.gov commenter name in a submitted comment, a CMS
Open-Payments manufacturer name, a ClinicalTrials.gov sponsor name) rather
than maintaining a separate mapping per source pair. This is the hardest
and most valuable piece of the long-term platform, per the project brief,
precisely because these six naming conventions won't agree with each
other without deliberate crosswalk work — expect it to need fuzzy
matching and manual review passes, not just exact-alias lookups, once
FCC/USPTO data (which don't share openFDA's applicant-name conventions at
all) are in scope.
