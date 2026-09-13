"""
Analysis/output layer: pivot-style views and standard competitive-
intelligence rollups over the `events` fact table.

The default pivot shape matches the convention used in the prior manual
sleep-appliance project: product code, then company, as row groupings;
years as columns; per-product-code subtotal rows and a grand total; blanks
(NaN) rather than zero for cells with no activity, since in a CI pivot an
explicit 0 and "no data" read very differently to a reviewer.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable, List, Optional

import pandas as pd

from .extract import extract_first
from .normalize import CompanyNormalizer, get_default_normalizer

SUBTOTAL_LABEL = "Subtotal"
GRAND_TOTAL_LABEL = "GRAND TOTAL"

# Column order for listing_510k(), grouped by the same four sections used
# to scope this feature: identification, applicant/company, submission &
# decision, and device classification. See docs/DATA_SOURCES.md for the
# per-field provenance (510(k)-native vs. classification-endpoint join vs.
# constructed).
LISTING_510K_COLUMNS = [
    # 1. Identification
    "k_number", "device_name", "summary_pdf_url",
    # 2. Applicant / company
    "applicant", "company_canonical", "company_category", "contact",
    "address_1", "address_2", "city", "state", "zip_code", "country_code",
    # 3. Submission & decision
    "date_received", "decision_date", "decision_code", "decision_description",
    "clearance_type", "third_party_flag", "expedited_review_flag",
    "advisory_committee", "advisory_committee_description", "statement_or_summary",
    # 4. Device classification (own openfda block, backfilled from the
    # classification endpoint join where the 510(k) record's own block is
    # missing a value)
    "product_code", "device_class", "regulation_number", "medical_specialty_description",
    "review_panel", "definition", "implant_flag", "life_sustain_support_flag", "gmp_exempt_flag",
]


def load_events(
    conn: sqlite3.Connection,
    segment: Optional[str] = None,
    endpoints: Optional[Iterable[str]] = None,
    event_types: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Load `events` rows into a DataFrame, optionally scoped to one
    segment (via segment_records) and/or a subset of endpoints/event types."""
    if segment:
        query = """
            SELECT e.* FROM events e
            JOIN segment_records sr
              ON sr.endpoint = e.endpoint AND sr.record_id = e.record_id
            WHERE sr.segment = ?
        """
        params: list = [segment]
    else:
        query = "SELECT * FROM events e WHERE 1=1"
        params = []

    if endpoints:
        endpoints = list(endpoints)
        query += f" AND e.endpoint IN ({','.join('?' * len(endpoints))})"
        params.extend(endpoints)
    if event_types:
        event_types = list(event_types)
        query += f" AND e.event_type IN ({','.join('?' * len(event_types))})"
        params.extend(event_types)

    df = pd.read_sql_query(query, conn, params=params)
    return df


def pivot_product_company_year(
    df: pd.DataFrame,
    company_col: str = "company_canonical",
    include_category: bool = False,
) -> pd.DataFrame:
    """Product code x company x year counts, with per-product-code
    subtotals and a grand total row, years as columns, blanks for zero."""
    working = df.dropna(subset=["product_code", "year"]).copy()
    working["year"] = working["year"].astype(int)
    working[company_col] = working[company_col].fillna("(Unknown)")

    group_cols = ["product_code", company_col]
    if include_category:
        group_cols.append("company_category")

    counts = (
        working.groupby(group_cols + ["year"])
        .size()
        .reset_index(name="count")
    )
    pivot = counts.pivot_table(
        index=group_cols, columns="year", values="count", aggfunc="sum"
    )
    pivot = pivot.reindex(sorted(pivot.columns), axis=1).sort_index()
    year_cols = list(pivot.columns)

    def _row(index_values: list, values) -> pd.DataFrame:
        return pd.DataFrame(
            [values],
            columns=year_cols,
            index=pd.MultiIndex.from_tuples([tuple(index_values)], names=pivot.index.names),
        )

    blocks = []
    for product_code, block in pivot.groupby(level="product_code", sort=True):
        blocks.append(block)
        subtotal_key = [product_code, SUBTOTAL_LABEL] + [""] * (len(group_cols) - 2)
        blocks.append(_row(subtotal_key, block[year_cols].sum(numeric_only=True).values))

    result = pd.concat(blocks) if blocks else pivot
    if not result.empty:
        grand_total = result.xs(SUBTOTAL_LABEL, level=company_col)[year_cols].sum(numeric_only=True)
        grand_key = [GRAND_TOTAL_LABEL] + [""] * (len(group_cols) - 1)
        result = pd.concat([result, _row(grand_key, grand_total.values)])

    return result


def new_entrants_by_year(
    df: pd.DataFrame,
    company_col: str = "company_canonical",
    by_product_code: bool = True,
) -> pd.DataFrame:
    """First-observed year for each (product_code, company) pair -- read
    this grouped by year to see who entered a category for the first time
    versus who was already active there."""
    working = df.dropna(subset=["year", company_col]).copy()
    working["year"] = working["year"].astype(int)

    group_cols = ["product_code", company_col] if by_product_code else [company_col]
    first_seen = (
        working.groupby(group_cols)["year"]
        .min()
        .reset_index()
        .rename(columns={"year": "first_year"})
        .sort_values(["first_year"] + group_cols)
    )
    return first_seen


def top_movers(
    df: pd.DataFrame,
    recent_years: int = 2,
    prior_years: int = 2,
    company_col: str = "company_canonical",
    as_of_year: Optional[int] = None,
) -> pd.DataFrame:
    """Compare each company's activity count in the most recent N years
    against the prior N years, ranked by absolute and percent change.
    Useful for "who's accelerating / who's pulling back" in a category.
    """
    working = df.dropna(subset=["year", company_col]).copy()
    working["year"] = working["year"].astype(int)
    if working.empty:
        return pd.DataFrame(columns=[company_col, "recent", "prior", "change", "pct_change"])

    latest_year = as_of_year or int(working["year"].max())
    recent_start = latest_year - recent_years + 1
    prior_start = recent_start - prior_years
    prior_end = recent_start - 1

    recent = (
        working[working["year"].between(recent_start, latest_year)]
        .groupby(company_col).size().rename("recent")
    )
    prior = (
        working[working["year"].between(prior_start, prior_end)]
        .groupby(company_col).size().rename("prior")
    )
    combined = pd.concat([recent, prior], axis=1).fillna(0)
    combined["change"] = combined["recent"] - combined["prior"]
    combined["pct_change"] = combined.apply(
        lambda r: (r["change"] / r["prior"] * 100) if r["prior"] else pd.NA, axis=1
    )
    return combined.reset_index().sort_values("change", ascending=False)


def category_growth_trends(
    df: pd.DataFrame,
    group_col: str = "product_code",
) -> pd.DataFrame:
    """Yearly event counts grouped by `group_col` (default product_code;
    pass 'company_category' for the strategic/PE-backed/startup rollup) --
    a quick read on which categories/segments are growing, flat, or shrinking.
    """
    working = df.dropna(subset=["year", group_col]).copy()
    working["year"] = working["year"].astype(int)
    counts = working.groupby([group_col, "year"]).size().reset_index(name="count")
    pivot = counts.pivot_table(index=group_col, columns="year", values="count", aggfunc="sum")
    return pivot.reindex(sorted(pivot.columns), axis=1)


def _load_raw_records(conn: sqlite3.Connection, endpoint: str, segment: Optional[str]) -> List[dict]:
    """Load and JSON-decode every cached raw record for one endpoint,
    optionally scoped to a segment via segment_records -- mirrors the
    segment-scoping pattern in load_events()."""
    if segment:
        rows = conn.execute(
            """
            SELECT r.raw_json FROM raw_records r
            JOIN segment_records sr ON sr.endpoint = r.endpoint AND sr.record_id = r.record_id
            WHERE sr.segment = ? AND r.endpoint = ?
            """,
            (segment, endpoint),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT raw_json FROM raw_records WHERE endpoint = ?", (endpoint,)
        ).fetchall()
    return [json.loads(row["raw_json"]) for row in rows]


def build_510k_summary_url(k_number: Optional[str]) -> Optional[str]:
    """Best-effort link to FDA's own hosted 510(k) Summary/Statement PDF.

    This is *constructed*, not returned by openFDA -- there is no API field
    for it. FDA's own hosting follows a directory-per-decade-and-year
    pattern keyed on the first two digits after the 'K' (e.g. K052737 ->
    .../pdf5/K052737.pdf, K193503 -> .../pdf19/K193503.pdf), but this was
    built without live access to verify it against accessdata.fda.gov, so
    treat a broken link as "confirm the pattern still holds," not as a bug
    in the surrounding pivot/listing data.
    """
    if not k_number:
        return None
    k_number = k_number.strip().upper()
    if len(k_number) < 3 or not k_number.startswith("K") or not k_number[1:3].isdigit():
        return None
    prefix = str(int(k_number[1:3]))
    return f"https://www.accessdata.fda.gov/cdrh_docs/pdf{prefix}/{k_number}.pdf"


def listing_510k(
    conn: sqlite3.Connection,
    segment: Optional[str] = None,
    normalizer: Optional[CompanyNormalizer] = None,
) -> pd.DataFrame:
    """Row-per-record 510(k) listing covering the identification,
    applicant/company, submission/decision, and device-classification
    fields (LISTING_510K_COLUMNS) for every cached 510(k) record in scope.

    Device-classification fields (review_panel, definition, implant_flag,
    life_sustain_support_flag, gmp_exempt_flag) only populate for product
    codes whose classification record has *also* been fetched for this
    segment -- left blank otherwise, not fetched on demand here, so this
    stays a read against already-cached data with no new API calls.
    """
    normalizer = normalizer or get_default_normalizer()

    classification_by_code = {}
    for rec in _load_raw_records(conn, "classification", segment):
        code = extract_first(rec, "product_code")
        if code:
            classification_by_code[code] = rec

    rows = []
    for rec in _load_raw_records(conn, "510k", segment):
        applicant_raw = extract_first(rec, "applicant")
        canonical = normalizer.normalize(applicant_raw) if applicant_raw else None
        product_code = extract_first(rec, "product_code")
        classification_rec = classification_by_code.get(product_code, {})

        rows.append({
            "k_number": extract_first(rec, "k_number"),
            "device_name": extract_first(rec, "device_name"),
            "summary_pdf_url": build_510k_summary_url(extract_first(rec, "k_number")),
            "applicant": applicant_raw,
            "company_canonical": canonical,
            "company_category": normalizer.category(canonical) if canonical else None,
            "contact": extract_first(rec, "contact"),
            "address_1": extract_first(rec, "address_1"),
            "address_2": extract_first(rec, "address_2"),
            "city": extract_first(rec, "city"),
            "state": extract_first(rec, "state"),
            "zip_code": extract_first(rec, "zip_code"),
            "country_code": extract_first(rec, "country_code"),
            "date_received": extract_first(rec, "date_received"),
            "decision_date": extract_first(rec, "decision_date"),
            "decision_code": extract_first(rec, "decision_code"),
            "decision_description": extract_first(rec, "decision_description"),
            "clearance_type": extract_first(rec, "clearance_type"),
            "third_party_flag": extract_first(rec, "third_party_flag"),
            "expedited_review_flag": extract_first(rec, "expedited_review_flag"),
            "advisory_committee": extract_first(rec, "advisory_committee"),
            "advisory_committee_description": extract_first(rec, "advisory_committee_description"),
            "statement_or_summary": extract_first(rec, "statement_or_summary"),
            "product_code": product_code,
            "device_class": extract_first(rec, "openfda.device_class")
                or extract_first(classification_rec, "device_class"),
            "regulation_number": extract_first(rec, "openfda.regulation_number")
                or extract_first(classification_rec, "regulation_number"),
            "medical_specialty_description": extract_first(rec, "openfda.medical_specialty_description"),
            "review_panel": extract_first(classification_rec, "review_panel"),
            "definition": extract_first(classification_rec, "definition"),
            "implant_flag": extract_first(classification_rec, "implant_flag"),
            "life_sustain_support_flag": extract_first(classification_rec, "life_sustain_support_flag"),
            "gmp_exempt_flag": extract_first(classification_rec, "gmp_exempt_flag"),
        })

    df = pd.DataFrame(rows, columns=LISTING_510K_COLUMNS)
    if not df.empty:
        df = df.sort_values(["decision_date", "k_number"], na_position="last").reset_index(drop=True)
    return df


def export_excel(sheets: dict, path: str) -> None:
    """Write a dict of {sheet_name: DataFrame} to a single .xlsx workbook.

    A DataFrame with a meaningful index (e.g. the pivot's product_code/
    company MultiIndex) is written with it; a flat, default RangeIndex
    (e.g. listing_510k's row-per-record table) is written without it, since
    a bare 0,1,2... row-number column adds nothing in a flat listing.
    """
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, sheet_df in sheets.items():
            include_index = not isinstance(sheet_df.index, pd.RangeIndex)
            sheet_df.to_excel(writer, sheet_name=sheet_name[:31], index=include_index)


def export_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path)
