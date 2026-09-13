# Market Intelligence

A durable, reusable medical device competitive intelligence platform.
Phase 1 (built) covers FDA's openFDA device datasets end to end: fetch,
normalize company names to current corporate parents, tag companies by
category, store locally, and analyze. It's designed from the start to
later merge in FCC equipment authorization data (connectivity), USPTO
patent data (IP position), regulations.gov rulemaking data (policy
signal), CMS data (coverage/payment/utilization), and ClinicalTrials.gov
data (pipeline signal) — see `docs/ROADMAP.md`.

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env   # add OPENFDA_API_KEY (free, raises rate limits)

python -m market_intel list-segments
python -m market_intel refresh --segment sleep_apnea_oral_appliances
python -m market_intel pivot --segment sleep_apnea_oral_appliances --out pivot.xlsx
python -m market_intel new-entrants --segment sleep_apnea_oral_appliances
python -m market_intel top-movers --segment sleep_apnea_oral_appliances
python -m market_intel category-trends --segment sleep_apnea_oral_appliances
python -m market_intel companies --segment sleep_apnea_oral_appliances
```

Or from Python/Jupyter:

```python
from market_intel import db, pipeline, analysis
from market_intel.segments import Segment

conn = db.connect()
segment = Segment.load("sleep_apnea_oral_appliances")
pipeline.run_segment(segment, conn=conn)

df = analysis.load_events(conn, segment="sleep_apnea_oral_appliances")
pivot = analysis.pivot_product_company_year(df)
```

Run `python -m pytest` to run the test suite (all network calls are
mocked, so this doesn't need internet access).

## Defining a segment

A segment is a YAML file under `config/segments/` scoping a competitive
slice by any combination of product codes, CFR regulation numbers, device
classes, applicant names, a date range, or a free-text openFDA query. See
`config/segments/sleep_apnea_oral_appliances.yaml` for a filled-in example
and `config/segments/ambulatory_cardiac_monitoring_TEMPLATE.yaml` for a
template with guidance on looking up product codes for a new category.
Full field docs live in the `Segment` class docstring in
`market_intel/segments.py`.

## Maintaining company normalization

`config/company_aliases.yaml` maps historical/subsidiary names to current
parent companies (e.g. Respironics -> Philips); `config/company_categories.yaml`
tags canonical companies with a category (Strategic/Incumbent Medtech,
PE-Backed Platform, Single-Product Startup, CDMO/Private-Label Filer, or
your own taxonomy). Both are plain YAML — edit and re-run
`python -m market_intel rebuild-events` to re-apply normalization to
already-cached data without hitting the API again. Run
`market_intel companies --segment <name>` to see which companies in a
segment are still `Unclassified`, as a worklist for what to add next.

## Layout

```
market_intel/
  sources/openfda/     openFDA REST client + endpoint field metadata
  segments.py           segment definition -> per-endpoint openFDA query
  normalize.py          company alias + category YAML lookups
  extract.py             dotted-path extraction over nested openFDA JSON
  db.py                 SQLite schema + upserts
  pipeline.py            fetch -> normalize -> persist orchestration
  analysis.py             pivots, new entrants, top movers, category trends
  cli.py                  command-line entry point
config/
  segments/              one YAML file per saved segment
  company_aliases.yaml
  company_categories.yaml
docs/
  ARCHITECTURE.md         layering, storage model, entity-resolution plan
  DATA_SOURCES.md         URLs, licensing, rate limits, per-source tooling notes
  ROADMAP.md              phase-by-phase plan for FCC/USPTO/regs.gov/CMS/CTgov
tests/
```

See `docs/ARCHITECTURE.md` for why storage is split into an immutable
`raw_records` cache and a derived `events` fact table, and
`docs/DATA_SOURCES.md` for what was evaluated (and why a thin openFDA
client was written rather than adopting an existing package) plus the
sourcing plan for every future-phase data source.

## Known limitation

This was built in a sandboxed environment with `api.fda.gov` blocked by
egress policy, so the openFDA client could not be validated against live
data — only against mocked HTTP responses (see `tests/`). Run
`python -m market_intel refresh --segment sleep_apnea_oral_appliances
--max-records 20` as a first smoke test once you have real network access,
and see the caveat in `docs/DATA_SOURCES.md` about spot-checking a couple
of nested field paths on the `event` endpoint.
