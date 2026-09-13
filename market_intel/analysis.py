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

import sqlite3
from typing import Iterable, Optional

import pandas as pd

SUBTOTAL_LABEL = "Subtotal"
GRAND_TOTAL_LABEL = "GRAND TOTAL"


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


def export_excel(sheets: dict, path: str) -> None:
    """Write a dict of {sheet_name: DataFrame} to a single .xlsx workbook."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, sheet_df in sheets.items():
            sheet_df.to_excel(writer, sheet_name=sheet_name[:31])


def export_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path)
