"""
Orchestration: fetch a segment from openFDA, normalize company names, and
persist both the raw records and the derived analysis-ready `events` rows.

This is the layer a CLI command or notebook cell calls; it does not itself
know about YAML files or SQLite schema details beyond what `segments.py`
and `db.py` expose.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import db
from .extract import extract_first, extract_id, extract_values
from .normalize import CompanyNormalizer, get_default_normalizer
from .segments import Segment
from .sources.openfda import ENDPOINTS, OpenFDAClient, OpenFDAError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _year_from_date(date_str: Optional[str]) -> Optional[int]:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


def build_events_for_record(endpoint_key: str, record_id: str, record: dict,
                             normalizer: CompanyNormalizer) -> List[dict]:
    """Turn one raw openFDA record into one or more `events` rows (one per
    distinct product code found on the record; a record with no discoverable
    product code still yields a single event with product_code=None so it
    isn't silently dropped from company/date-level views)."""
    spec = ENDPOINTS[endpoint_key]

    company_raw = extract_first(record, spec.company_field)
    company_canonical = normalizer.normalize(company_raw) if company_raw else None
    company_category = normalizer.category(company_canonical) if company_canonical else None

    event_date = extract_first(record, spec.date_field)
    year = _year_from_date(event_date)

    product_codes: List[Optional[str]] = []
    for path in spec.product_code_paths:
        for v in extract_values(record, path):
            pc = str(v).strip().upper()
            if pc and pc not in product_codes:
                product_codes.append(pc)
    if not product_codes:
        product_codes = [None]

    events = []
    for pc in product_codes:
        events.append({
            "id": f"{endpoint_key}:{record_id}:{pc or ''}",
            "endpoint": endpoint_key,
            "event_type": spec.event_type,
            "record_id": record_id,
            "product_code": pc,
            "company_raw": company_raw,
            "company_canonical": company_canonical,
            "company_category": company_category,
            "event_date": event_date,
            "year": year,
        })
    return events


def run_segment(
    segment: Segment,
    conn: Optional[sqlite3.Connection] = None,
    client: Optional[OpenFDAClient] = None,
    normalizer: Optional[CompanyNormalizer] = None,
    endpoints: Optional[List[str]] = None,
    max_records_per_endpoint: Optional[int] = None,
) -> Dict[str, dict]:
    """Fetch `segment` from openFDA across its configured endpoints (or an
    explicit override list), normalize, and persist. Returns a per-endpoint
    stats dict: {endpoint: {"status": "ok"|"error"|"skipped", "records": n,
    "error": str|None}}.

    Safe to re-run: raw records and events are upserted by id, and each
    endpoint's segment links are cleared and rebuilt on each run so removed
    records (e.g. a withdrawn 510(k)) don't linger in a segment's view
    indefinitely -- though the underlying raw_records row is kept, since
    another segment may still reference it.
    """
    own_conn = conn is None
    conn = conn or db.connect()
    client = client or OpenFDAClient()
    normalizer = normalizer or get_default_normalizer()

    endpoint_keys = endpoints or segment.endpoints
    now = _now_iso()
    db.upsert_segment(conn, segment.name, segment.as_dict(), now)

    stats: Dict[str, dict] = {}
    companies_seen: Dict[str, str] = {}

    for endpoint_key in endpoint_keys:
        if endpoint_key not in ENDPOINTS:
            stats[endpoint_key] = {"status": "error", "records": 0, "error": "unknown endpoint"}
            continue

        query = segment.query_for_endpoint(endpoint_key)
        if query is None:
            logger.info("Segment %r has no applicable filter for endpoint %r; skipping.",
                        segment.name, endpoint_key)
            stats[endpoint_key] = {"status": "skipped", "records": 0, "error": None}
            continue

        spec = ENDPOINTS[endpoint_key]
        db.clear_segment_links_for_endpoint(conn, segment.name, endpoint_key)

        record_count = 0
        try:
            for record in client.search(endpoint_key, search=query,
                                         max_records=max_records_per_endpoint):
                record_id = extract_id(record, spec.id_fields)
                if not record_id:
                    logger.warning("Skipping %s record with no derivable id: %r",
                                    endpoint_key, list(record.keys())[:10])
                    continue

                db.upsert_raw_record(conn, endpoint_key, record_id, record, now)
                db.link_segment_record(conn, segment.name, endpoint_key, record_id)

                for event in build_events_for_record(endpoint_key, record_id, record, normalizer):
                    db.upsert_event(conn, event)
                    if event["company_canonical"]:
                        companies_seen[event["company_canonical"]] = event["company_category"]

                record_count += 1

            db.log_fetch(conn, segment.name, endpoint_key, now, record_count,
                         {"search": query}, "ok")
            stats[endpoint_key] = {"status": "ok", "records": record_count, "error": None}

        except OpenFDAError as e:
            logger.error("openFDA error fetching %s for segment %r: %s",
                         endpoint_key, segment.name, e)
            db.log_fetch(conn, segment.name, endpoint_key, now, record_count,
                         {"search": query}, "error")
            stats[endpoint_key] = {"status": "error", "records": record_count, "error": str(e)}

    for canonical_name, category in companies_seen.items():
        db.upsert_company(conn, canonical_name, category, now)

    if own_conn:
        conn.close()

    return stats


def rebuild_events(conn: Optional[sqlite3.Connection] = None,
                    normalizer: Optional[CompanyNormalizer] = None) -> int:
    """Recompute the entire `events` table from `raw_records`, without
    re-fetching from openFDA. Use this after editing company_aliases.yaml
    or company_categories.yaml so existing data picks up the new mapping.
    """
    import json

    own_conn = conn is None
    conn = conn or db.connect()
    normalizer = normalizer or get_default_normalizer()
    normalizer.reload()

    count = 0
    for endpoint_key in ENDPOINTS:
        db.delete_events_for_endpoint(conn, endpoint_key)
        for row in conn.execute(
            "SELECT record_id, raw_json FROM raw_records WHERE endpoint = ?", (endpoint_key,)
        ).fetchall():
            record = json.loads(row["raw_json"])
            for event in build_events_for_record(endpoint_key, row["record_id"], record, normalizer):
                db.upsert_event(conn, event)
                count += 1
    conn.commit()
    if own_conn:
        conn.close()
    return count
