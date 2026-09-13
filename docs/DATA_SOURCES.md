# Data sources

## Phase 1 (built): openFDA — device endpoints

- **What**: FDA's public device data — 510(k) clearances, PMA approvals,
  device classification/product codes, recall enforcement reports, adverse
  event (MAUDE) reports, UDI/GUDID records, and establishment
  registrations & listings.
- **API docs**: https://open.fda.gov/apis/device/
- **Base URL**: `https://api.fda.gov`
- **Auth**: none required; a free API key
  (https://open.fda.gov/apis/authentication/) raises the rate limit from
  40 req/min & 1,000/day (unkeyed) to 240 req/min & 120,000/day. Set
  `OPENFDA_API_KEY` (see `.env.example`).
- **License**: public domain (U.S. Government work). openFDA's own terms:
  https://open.fda.gov/license/ — attribution is appreciated but not
  required; openFDA explicitly disclaims warranty of completeness/accuracy,
  so recall/adverse-event signal here is a screening tool, not a
  diligence-final source.
- **Pagination limits**: `limit` capped at 1000/request; `skip` effectively
  capped around 25,000 (openFDA rejects `skip+limit` beyond that). A query
  returning more than ~26,000 records needs to be sliced (e.g. by
  `date_start`/`date_end` on the segment) rather than paged through in one
  pass — `market_intel/sources/openfda/client.py` logs a warning and stops
  cleanly if a query hits this ceiling rather than silently truncating.
- **Existing tools evaluated before building a client** (see
  `market_intel/sources/openfda/client.py` module docstring for the fuller
  rationale): no actively-maintained, device-endpoint-complete Python
  package was found on PyPI (`pyfda` couldn't be confirmed as either);
  `FDA/openfda` on GitHub is the ETL pipeline that *produces* api.fda.gov,
  not a consumer client; `rOpenHealth/openfda` is R. Paid Apify-style
  wrappers around this free API were deliberately not used. Given the
  device endpoints' small, stable, documented REST surface, a thin
  purpose-built client (`OpenFDAClient`) was the lower-risk choice — it can
  be swapped for a maintained library later without touching the segment,
  normalization, storage, or analysis layers, all of which only depend on
  `OpenFDAClient.search()`'s plain dict/list output.
- **Caveat on nested field paths**: this was built without live network
  access to `api.fda.gov` from the build environment (blocked by sandbox
  egress policy). `product_code`, `applicant`/`recalling_firm`/
  `manufacturer_name`, and the primary date fields per endpoint match
  openFDA's long-documented examples and are high-confidence. The
  `device_class`/`regulation_number` nested paths on `event` in particular
  (`device.openfda.device_class`, `device.openfda.regulation_number`)
  follow the same convention as the well-documented
  `device.openfda.product_code` but should be spot-checked against
  https://open.fda.gov/apis/device/event/searchable-fields/ (and the
  equivalent pages for other endpoints) on first real use — see
  `market_intel/sources/openfda/endpoints.py`.

## Phase 2 (scoped, not built): FCC equipment authorization

- **What**: resolves FCC ID → grantee/manufacturer and per-device radio
  details, for flagging which cleared devices are wireless/connected.
- **Sources**:
  - EAS Grantee Registrations (Socrata dataset, `opendata.fcc.gov`, id
    `3b3k-34jp`).
  - OET Equipment Authorization search, for per-device detail beyond the
    grantee registration.
- **Client**: use `sodapy` (https://github.com/xmunoz/sodapy — PyPI
  `sodapy`) for the Socrata dataset; it's the standard, actively-used
  Python client for any Socrata-backed open-data portal, which includes
  both `opendata.fcc.gov` here and `data.cms.gov` in the CMS phase below —
  one client, two agencies, per the project brief's instruction to avoid a
  per-agency Socrata client. Community FCC-ID lookup scripts exist on
  GitHub but are mostly single-purpose scrapers; evaluate freshness before
  adopting any of them over a direct OET search call.
- **Known hard problem, flagged for that phase**: FCC data keys off FCC ID
  / grantee code, not company name — expect grantee names to need their own
  normalization pass before they can join against the `companies` table
  this platform already builds from openFDA applicant names. This is the
  first instance of the cross-source entity-resolution problem described
  in the Unified Entity Model section of `ROADMAP.md`.

## Phase 3 (scoped, not built): USPTO / patent data via PatentsView

- **What**: assignee organization, CPC classification, filing/grant dates,
  citation counts — an IP-density/IP-trend dimension per normalized parent
  company and per CPC code.
- **Source**: PatentsView PatentSearch API, `https://search.patentsview.org`
  — **not** the legacy USPTO ODP/PatentsView API
  (`api.patentsview.org`), which returned HTTP 410 Gone as of May 1, 2025.
  Confirmed via GitHub search during this build (see
  `PatentsView/PatentSearch-API` for the current API's own source).
- **Client**: no confirmed actively-maintained Python client targeting the
  *new* PatentSearch API was found during this pass (`PatentsView-APIWrapper`
  repos found on GitHub predate the migration and should be checked against
  the new base URL/schema before reuse, not assumed compatible). The old
  rOpenSci `patentsview` R package remains a good reference for query
  semantics even though it's R. Budget time to verify a client's target API
  version before adopting it in this phase, or write a thin client
  following the same pattern as `OpenFDAClient`.

## Phase 4 (scoped, not built): regulations.gov — reuse `regs_agent.py`

- **What**: dockets, documents, and comments relevant to a product code's
  or company's rulemaking exposure (e.g. coverage/reimbursement rulemakings,
  device-classification rulemakings), joined onto the same product-code and
  company dimensions used elsewhere in this platform.
- **Source**: regulations.gov v4 API.
- **Reuse, don't rebuild**: the existing `regs_agent.py` (SQLite-backed,
  FTS5 full-text search, rate-limit-aware) already implements this end to
  end. This phase's work is an *integration* layer — a join table or view
  mapping `regs_agent.py`'s dockets/documents to this platform's
  `product_code`/`company_canonical` dimensions (e.g. by matching
  docket/document text against product code names or company names) — not
  a new regulations.gov client. Call `regs_agent.py` as a library or
  subprocess rather than duplicating its fetch/refresh/backoff logic here.

## Phase 5 (scoped, not built): CMS coverage, payment & utilization

- **What**: NCD/LCD coverage determinations, Open Payments, DMEPOS and
  provider utilization data — a reimbursement/adoption signal alongside
  clearance-only data from openFDA.
- **Sources**:
  - `data.cms.gov` Socrata datasets (DMEPOS, Open Payments, utilization) —
    same `sodapy` client as the FCC phase.
  - CMS Coverage API (NCDs/LCDs) — build a direct client here even though
    an MCP connector may cover this in some sessions, so the platform isn't
    dependent on that connector's availability.
  - NPPES registry API — reuse the approach/code from the existing
    large-scale NPPES enrichment project rather than writing a new client.
- **Integration point**: join by product code (DMEPOS HCPCS-to-product-code
  crosswalk needed) and by normalized company (manufacturer on Open
  Payments / DMEPOS supplier records).

## Phase 6 (scoped, not built): ClinicalTrials.gov — pipeline signal

- **What**: sponsor/collaborator and trial-phase data as a forward-looking
  leading indicator, ahead of any openFDA clearance.
- **Source**: ClinicalTrials.gov API v2 (JSON; the v1 XML API is retired).
- **Client evaluated**: `pytrials` (https://github.com/jvfe/pytrials, PyPI
  `pytrials`) is the most established option and documents v2 base-URL
  support, though its full-studies endpoint is capped at 100 records per
  call and its study-fields endpoint at 1000 — plan around that ceiling for
  a sponsor with a large pipeline. A specific, more modern "pyctrials"
  typed client did not turn up in this pass; re-check before this phase
  starts in case one has since appeared, and compare it against `pytrials`
  on v2 completeness and pagination handling before choosing.
- **Integration point**: normalize sponsor/collaborator names through the
  same `market_intel.normalize` alias table used for openFDA applicants.
