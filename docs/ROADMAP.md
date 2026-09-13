# Roadmap

## Phase 1 — openFDA (this build)

- [x] `sources/openfda/client.py` — paginated, rate-limit-aware REST client
      for all seven device endpoints.
- [x] `segments.py` — YAML-defined segments (product codes, CFR numbers,
      device classes, applicants, date range, free text).
- [x] `normalize.py` — company alias + category YAML lookups.
- [x] `db.py` — SQLite schema (raw_records / segment_records / events /
      companies / segments / fetch_log).
- [x] `pipeline.py` — fetch -> normalize -> persist orchestration,
      idempotent re-runs, `rebuild_events()` for normalization-only updates.
- [x] `analysis.py` — product x company x year pivot with subtotals, new
      entrants by year, top movers, category growth trends, CSV/Excel
      export.
- [x] `cli.py` — `market_intel refresh|pivot|new-entrants|top-movers|
      category-trends|companies|list-segments|rebuild-events`.
- [ ] Live validation against `api.fda.gov` — not possible from the sandbox
      this was built in (egress-blocked); run `market_intel refresh
      --segment sleep_apnea_oral_appliances --max-records 20` as a first
      smoke test once you have network access, and spot-check the nested
      field paths flagged in `docs/DATA_SOURCES.md`.

## Phase 2 — FCC equipment authorization (connectivity)

- Add `sources/fcc/client.py` using `sodapy` against the EAS Grantee
  Registrations Socrata dataset, plus an OET equipment-authorization-search
  call for per-device radio detail.
- Extend `db.py` with an `fcc_grants` raw table and an `events`-style
  derived table (or extend `events` with an `endpoint='fcc'` row type,
  matching the existing pattern).
- Entity resolution: FCC grantee name -> `company_aliases.yaml` canonical
  name. Expect this to require fuzzy matching, not just exact alias
  lookups — budget for a manual-review pass on the first run.
- New analysis view: "% of cleared devices in this segment with an FCC
  grant on file" (a connectivity/wireless proxy), joined by
  product_code + normalized company + approximate date window (FCC grant
  date won't align exactly with 510(k)/PMA decision date).

## Phase 3 — USPTO / PatentsView (IP position)

- Add `sources/patentsview/client.py` against
  `https://search.patentsview.org` (confirm current auth requirements —
  the new PatentSearch API may require a free API key where the legacy one
  did not).
- Key fields: assignee organization, CPC codes, filing/grant dates,
  citation counts.
- New analysis view: patent filings/grants per normalized company per
  year, alongside the existing clearance-cadence view; CPC-code density by
  product-code segment as an IP-crowding signal.

## Phase 4 — regulations.gov integration (policy signal)

- Do not rebuild a regulations.gov client. Call the existing `regs_agent.py`
  (its own SQLite DB, FTS5 search, rate-limit backoff) as a library or
  subprocess.
- Build a join layer: map `regs_agent.py` dockets/documents to this
  platform's `product_code`/`company_canonical` dimensions, likely via
  keyword/product-code-name matching against docket titles and document
  text (regs_agent's FTS5 index is the natural place to run that search
  from).
- New analysis view: "open or recent rulemaking relevant to this segment,"
  surfaced alongside clearance activity for the same product codes.

## Phase 5 — CMS coverage, payment & utilization (reimbursement signal)

- Add `sources/socrata/client.py` as a *shared* thin wrapper around
  `sodapy`, parameterized by domain — used for both `data.cms.gov` (this
  phase) and `opendata.fcc.gov` (Phase 2), per the project brief's
  instruction not to write one client per Socrata-backed agency.
- Add a direct CMS Coverage API (NCD/LCD) client, independent of any MCP
  connector, so this platform doesn't depend on connector availability.
- Reuse the existing NPPES enrichment project's approach for provider-level
  joins rather than writing a new NPPES client.
- New analysis view: coverage status + Open Payments/DMEPOS volume by
  product code and normalized manufacturer, next to clearance and (once
  Phase 2 lands) connectivity data.

## Phase 6 — ClinicalTrials.gov (pipeline signal)

- Add `sources/clinicaltrials/client.py`, starting from `pytrials` if it
  proves adequate for v2 (re-evaluate against any newer typed v2 client
  first — see `docs/DATA_SOURCES.md`).
- Normalize sponsor/collaborator names through the same
  `company_aliases.yaml` table used for openFDA applicants.
- New analysis view: active trial count/phase by normalized company and
  condition/intervention, as a leading indicator laid next to the lagging
  clearance-cadence view from Phase 1.

## Unified entity model (cross-cutting, ongoing)

Every phase above feeds the *same* `companies` table and
`company_aliases.yaml`, rather than a separate crosswalk per source pair
(see `docs/ARCHITECTURE.md`). Treat this as the platform's actual
long-term asset — the individual source integrations are comparatively
mechanical once each API is understood, but a company name resolving
correctly across FDA/FCC/USPTO/regulations.gov/CMS/ClinicalTrials.gov
naming conventions is the piece that makes a single company/category
rollup possible, and it will need ongoing manual curation, not a one-time
build.
