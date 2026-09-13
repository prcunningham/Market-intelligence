"""
Download FDA's own hosted 510(k) Summary/Statement PDFs for records
already in a segment's listing (see analysis.build_510k_summary_url).

This deliberately does not go through OpenFDAClient: these PDFs are served
from accessdata.fda.gov, a different host than api.fda.gov, as static
files with no JSON API, no documented rate limit, and no distinction
between "temporarily down" and "this URL was never valid." A 404 here is
expected and common -- the URL is a best-effort construction from the
k_number, not a field openFDA actually returns -- so it's treated as a
plain "not found" outcome, not a retryable failure. Only a transient
server error gets a couple of short retries.

Typical use: fetch a segment's 510(k) listing (analysis.listing_510k),
optionally trim it to the N records you actually want, then hand that
DataFrame to download_510k_summaries() to pull each one's PDF into a
local folder -- e.g. for a follow-up LLM comparison pass over the actual
submission documents rather than just the structured metadata.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


def download_pdf(
    url: str,
    dest: Path,
    session: Optional[requests.Session] = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> str:
    """Download one PDF to `dest`. Returns 'downloaded', 'not_found', or 'error'."""
    session = session or requests.Session()
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.get(url, timeout=timeout)
        except requests.exceptions.RequestException as e:
            if attempt > max_retries:
                logger.warning("Failed to download %s after %d attempts: %s", url, attempt, e)
                return "error"
            time.sleep(1.5 * attempt)
            continue

        if resp.status_code == 200:
            looks_like_pdf = (
                "pdf" in resp.headers.get("Content-Type", "").lower()
                or resp.content[:4] == b"%PDF"
            )
            if not looks_like_pdf:
                logger.warning(
                    "%s returned 200 but doesn't look like a PDF (content-type=%s) -- "
                    "likely an FDA error page served with a 200 status; skipping.",
                    url, resp.headers.get("Content-Type"),
                )
                return "error"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return "downloaded"

        if resp.status_code == 404:
            return "not_found"

        if resp.status_code >= 500 and attempt <= max_retries:
            time.sleep(1.5 * attempt)
            continue

        logger.warning("Unexpected status %s downloading %s", resp.status_code, url)
        return "error"


def download_510k_summaries(
    listing_df: pd.DataFrame,
    out_dir: str,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Download each row's `summary_pdf_url` in `listing_df` (as produced by
    analysis.listing_510k()) to `out_dir/{k_number}.pdf`. Trim `listing_df`
    to however many records you want *before* calling this -- it downloads
    every row it's given, with no limit of its own.

    Returns a DataFrame with one row per input record: k_number, device_name,
    applicant, url, status ('downloaded' | 'not_found' | 'error' | 'no_url'),
    and path (set only when status == 'downloaded').
    """
    out_path = Path(out_dir)
    session = session or requests.Session()

    results = []
    for _, row in listing_df.iterrows():
        k_number = row.get("k_number")
        url = row.get("summary_pdf_url")
        # A missing value here can arrive as None, an empty string, or (if
        # pandas coerced a lone None to float NaN, which is truthy in
        # Python) NaN -- pd.isna() catches all three, `not url` catches ""
        # without pd.isna() misfiring on a normal non-empty string.
        if pd.isna(url) or not url:
            results.append({
                "k_number": k_number, "device_name": row.get("device_name"),
                "applicant": row.get("applicant"), "url": url,
                "status": "no_url", "path": None,
            })
            continue

        dest = out_path / f"{k_number}.pdf"
        status = download_pdf(url, dest, session=session)
        results.append({
            "k_number": k_number,
            "device_name": row.get("device_name"),
            "applicant": row.get("applicant"),
            "url": url,
            "status": status,
            "path": str(dest) if status == "downloaded" else None,
        })

    return pd.DataFrame(
        results,
        columns=["k_number", "device_name", "applicant", "url", "status", "path"],
    )
