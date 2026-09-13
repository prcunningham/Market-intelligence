"""
openFDA device endpoint metadata.

Source: https://open.fda.gov/apis/device/
License: openFDA data is public domain (U.S. Government work). See
https://open.fda.gov/license/ for the openFDA terms of use (attribution
appreciated but not required; no warranty of accuracy or completeness).

Rate limits (see https://open.fda.gov/apis/authentication/):
  - Without an API key: 40 requests/minute, 1,000 requests/day per IP.
  - With a free API key (https://open.fda.gov/apis/authentication/):
    240 requests/minute, 120,000 requests/day per key.
  - Max `limit` per request is 1000. `skip` is capped at 25,000 (i.e. you
    cannot page past the 26,000th result with skip/limit alone); very large
    result sets need date-range slicing to stay under that ceiling. openFDA
    also supports `search_after`-based deep pagination on some indices, but
    it is not uniformly available across device endpoints, so this client
    uses skip/limit plus caller-driven date slicing instead.

Each endpoint definition below captures just enough shape for the
segment/normalization/storage layers to treat all seven device endpoints
uniformly:

  path            - URL path appended to https://api.fda.gov
  id_fields       - record fields combined (with '-') to form a stable
                    record_id unique within the endpoint
  company_field   - dotted path to the applicant/manufacturer/firm name
                    used for company normalization (top-level events use
                    the first match)
  product_code_paths - list of dotted paths that may contain product
                    code(s) (string or list of strings/dicts); all are
                    collected and de-duplicated per record
  date_field      - dotted path to the primary date used for year-based
                    analysis
  event_type      - short label used in the unified `events` fact table
  product_code_field - the openFDA search field name used to filter this
                    endpoint by product code (varies by endpoint because
                    product code sometimes lives under a nested `openfda`
                    object populated by FDA's own record-linking, not the
                    submitter's original data)
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    path: str
    id_fields: List[str]
    company_field: Optional[str]
    product_code_paths: List[str]
    product_code_search_field: Optional[str]
    date_field: Optional[str]
    event_type: str
    description: str = ""
    # openFDA search-field names for device class / CFR regulation number
    # filters. These are None where the nested path isn't reliably present
    # on the endpoint (udi, registrationlisting) -- filtering there falls
    # back to product_code only. The 510k/pma/classification/enforcement
    # paths below match openFDA's documented examples; the event (MAUDE)
    # nested `device.openfda.*` paths follow the same convention as
    # `device.openfda.product_code` but, like all nested-field paths here,
    # should be spot-checked against
    # https://open.fda.gov/apis/device/<endpoint>/searchable-fields/ before
    # relying on them in a production run, since this was built without
    # live network access to openFDA.
    device_class_field: Optional[str] = None
    regulation_number_field: Optional[str] = None


ENDPOINTS = {
    "510k": EndpointSpec(
        key="510k",
        path="/device/510k.json",
        id_fields=["k_number"],
        company_field="applicant",
        product_code_paths=["product_code"],
        product_code_search_field="product_code",
        date_field="decision_date",
        event_type="510k_clearance",
        description="Premarket notification (510(k)) clearances",
        device_class_field="openfda.device_class",
        regulation_number_field="openfda.regulation_number",
    ),
    "pma": EndpointSpec(
        key="pma",
        path="/device/pma.json",
        id_fields=["pma_number", "supplement_number"],
        company_field="applicant",
        product_code_paths=["product_code"],
        product_code_search_field="product_code",
        date_field="decision_date",
        event_type="pma_approval",
        description="Premarket approvals (PMA) and supplements",
        device_class_field="openfda.device_class",
        regulation_number_field="openfda.regulation_number",
    ),
    "classification": EndpointSpec(
        key="classification",
        path="/device/classification.json",
        id_fields=["product_code"],
        company_field=None,
        product_code_paths=["product_code"],
        product_code_search_field="product_code",
        date_field=None,
        event_type="classification",
        description="Device classification / product code reference data",
        device_class_field="device_class",
        regulation_number_field="regulation_number",
    ),
    "enforcement": EndpointSpec(
        key="enforcement",
        path="/device/enforcement.json",
        id_fields=["recall_number"],
        company_field="recalling_firm",
        product_code_paths=["openfda.product_code"],
        product_code_search_field="openfda.product_code",
        date_field="recall_initiation_date",
        event_type="recall",
        description="Recall enforcement reports",
        device_class_field="openfda.device_class",
        regulation_number_field="openfda.regulation_number",
    ),
    "event": EndpointSpec(
        key="event",
        path="/device/event.json",
        id_fields=["mdr_report_key"],
        company_field="manufacturer_name",
        product_code_paths=["device.openfda.product_code"],
        product_code_search_field="device.openfda.product_code",
        date_field="date_received",
        event_type="adverse_event",
        description="Adverse event / MDR (MAUDE) reports",
        device_class_field="device.openfda.device_class",
        regulation_number_field="device.openfda.regulation_number",
    ),
    "udi": EndpointSpec(
        key="udi",
        path="/device/udi.json",
        id_fields=["public_device_record_key"],
        company_field="company_name",
        product_code_paths=["product_codes.code"],
        product_code_search_field="product_codes.code",
        date_field="publish_date",
        event_type="udi_record",
        description="Unique Device Identification (GUDID) records",
    ),
    "registrationlisting": EndpointSpec(
        key="registrationlisting",
        path="/device/registrationlisting.json",
        id_fields=["registration.registration_number", "k_number"],
        company_field="registration.name",
        product_code_paths=["products.product_code"],
        product_code_search_field="products.product_code",
        date_field=None,
        event_type="registration",
        description="Establishment registrations & device listings",
    ),
}

# Endpoints that make sense as the default set for a competitive-intelligence
# segment pull (excludes registrationlisting/udi, which are heavier and more
# useful for the connectivity/entity-resolution phase than for a first pass).
DEFAULT_ENDPOINTS = ["510k", "pma", "classification", "enforcement", "event"]
