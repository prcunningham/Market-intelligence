"""
SQLite storage layer.

Mirrors the pattern used in the existing regulations.gov agent (regs_agent.py):
a single local SQLite file, explicit schema, upsert-safe writes, so repeated
runs are idempotent and analyses don't need to re-hit the API every time.

Two layers of data are kept deliberately separate:

  raw_records     Immutable-ish cache of exactly what openFDA returned, one
                   row per (endpoint, record_id), full record as JSON. This
                   is the source of truth; anything upstream of it can be
                   recomputed from it.
  events          A derived, denormalized fact table: one row per
                   (endpoint, record_id, product_code) combination, with
                   normalized company name/category and a parsed year. This
                   is what analysis.py queries. It is fully rebuildable from
                   raw_records via rebuild_events(), so normalization-rule
                   changes (new alias, new category) don't require
                   re-fetching from openFDA -- just a rebuild.

segment_records links segments to the raw records they pulled in, so the
same underlying FDA record can be shared by multiple overlapping segments
without duplicating storage, and a segment can be re-scoped without
re-fetching records already cached from another segment's pull.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_records (
    endpoint    TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    raw_json    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (endpoint, record_id)
);

CREATE TABLE IF NOT EXISTS segment_records (
    segment     TEXT NOT NULL,
    endpoint    TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    PRIMARY KEY (segment, endpoint, record_id)
);
CREATE INDEX IF NOT EXISTS idx_segment_records_segment ON segment_records(segment);

CREATE TABLE IF NOT EXISTS events (
    id                  TEXT PRIMARY KEY,
    endpoint            TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    record_id           TEXT NOT NULL,
    product_code        TEXT,
    company_raw         TEXT,
    company_canonical   TEXT,
    company_category    TEXT,
    event_date          TEXT,
    year                INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_product_year ON events(product_code, year);
CREATE INDEX IF NOT EXISTS idx_events_company ON events(company_canonical);
CREATE INDEX IF NOT EXISTS idx_events_endpoint ON events(endpoint);

CREATE TABLE IF NOT EXISTS companies (
    canonical_name  TEXT PRIMARY KEY,
    category        TEXT,
    first_seen      TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    name            TEXT PRIMARY KEY,
    definition_json TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    segment        TEXT NOT NULL,
    endpoint       TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    record_count   INTEGER NOT NULL,
    params_json    TEXT,
    status         TEXT NOT NULL
);
"""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def cursor(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def upsert_raw_record(conn: sqlite3.Connection, endpoint: str, record_id: str,
                       raw: Dict[str, Any], fetched_at: str) -> None:
    conn.execute(
        """
        INSERT INTO raw_records (endpoint, record_id, raw_json, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint, record_id) DO UPDATE SET
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
        """,
        (endpoint, record_id, json.dumps(raw), fetched_at),
    )


def link_segment_record(conn: sqlite3.Connection, segment: str, endpoint: str,
                         record_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO segment_records (segment, endpoint, record_id)
        VALUES (?, ?, ?)
        """,
        (segment, endpoint, record_id),
    )


def clear_segment_links(conn: sqlite3.Connection, segment: str) -> None:
    conn.execute("DELETE FROM segment_records WHERE segment = ?", (segment,))


def clear_segment_links_for_endpoint(conn: sqlite3.Connection, segment: str, endpoint: str) -> None:
    conn.execute(
        "DELETE FROM segment_records WHERE segment = ? AND endpoint = ?",
        (segment, endpoint),
    )


def upsert_event(conn: sqlite3.Connection, event: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO events (id, endpoint, event_type, record_id, product_code,
                             company_raw, company_canonical, company_category,
                             event_date, year)
        VALUES (:id, :endpoint, :event_type, :record_id, :product_code,
                :company_raw, :company_canonical, :company_category,
                :event_date, :year)
        ON CONFLICT(id) DO UPDATE SET
            company_canonical = excluded.company_canonical,
            company_category = excluded.company_category,
            event_date = excluded.event_date,
            year = excluded.year
        """,
        event,
    )


def delete_events_for_endpoint(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute("DELETE FROM events WHERE endpoint = ?", (endpoint,))


def upsert_company(conn: sqlite3.Connection, canonical_name: str, category: str,
                    first_seen: str) -> None:
    conn.execute(
        """
        INSERT INTO companies (canonical_name, category, first_seen, notes)
        VALUES (?, ?, ?, NULL)
        ON CONFLICT(canonical_name) DO UPDATE SET category = excluded.category
        """,
        (canonical_name, category, first_seen),
    )


def upsert_segment(conn: sqlite3.Connection, name: str, definition: Dict[str, Any],
                    now: str) -> None:
    conn.execute(
        """
        INSERT INTO segments (name, definition_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            definition_json = excluded.definition_json,
            updated_at = excluded.updated_at
        """,
        (name, json.dumps(definition), now, now),
    )


def log_fetch(conn: sqlite3.Connection, segment: str, endpoint: str, fetched_at: str,
              record_count: int, params: Dict[str, Any], status: str) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (segment, endpoint, fetched_at, record_count, params_json, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (segment, endpoint, fetched_at, record_count, json.dumps(params), status),
    )


def raw_records_for_segment(conn: sqlite3.Connection, segment: str, endpoint: Optional[str] = None
                             ) -> Iterable[sqlite3.Row]:
    if endpoint:
        return conn.execute(
            """
            SELECT r.endpoint, r.record_id, r.raw_json FROM raw_records r
            JOIN segment_records sr ON sr.endpoint = r.endpoint AND sr.record_id = r.record_id
            WHERE sr.segment = ? AND r.endpoint = ?
            """,
            (segment, endpoint),
        ).fetchall()
    return conn.execute(
        """
        SELECT r.endpoint, r.record_id, r.raw_json FROM raw_records r
        JOIN segment_records sr ON sr.endpoint = r.endpoint AND sr.record_id = r.record_id
        WHERE sr.segment = ?
        """,
        (segment,),
    ).fetchall()
