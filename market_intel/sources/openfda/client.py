"""
Thin, dependency-light client for openFDA's device endpoints.

Why a hand-rolled client: as of this writing there is no actively maintained
Python package on PyPI purpose-built for openFDA's *device* endpoints (the
one candidate found, `pyfda`, could not be confirmed as maintained or
device-endpoint-complete; the official `FDA/openfda` repo is the ETL that
*produces* api.fda.gov, not a client for consuming it; `rOpenHealth/openfda`
is R, not Python). The paid Apify wrappers that surfaced in a search are
markup on top of a free, keyless government API and are deliberately not
used here. openFDA's REST surface is small, stable, and documented
(https://open.fda.gov/apis/), so a thin client is lower risk than adopting a
narrow or unmaintained dependency. If a well-maintained Python client
appears later, it can replace this module without touching the segment,
normalization, storage, or analysis layers above it, since all of those talk
to `OpenFDAClient.search()`'s plain dict/list output.

Docs: https://open.fda.gov/apis/device/
Auth: https://open.fda.gov/apis/authentication/ (free API key, optional but
      raises rate limits substantially; set OPENFDA_API_KEY)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

from .endpoints import ENDPOINTS, EndpointSpec

logger = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov"
MAX_LIMIT = 1000
# openFDA rejects skip + limit beyond ~26000; treat 25000 as the safe
# ceiling for a single skip/limit walk. Segments that exceed this for a
# given query need to be sliced further (e.g. by date range) by the caller.
MAX_SKIP = 25000


class OpenFDAError(RuntimeError):
    """Raised for non-recoverable openFDA API errors."""


class OpenFDAClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        session: Optional[requests.Session] = None,
        max_retries: int = 5,
        backoff_base: float = 1.5,
        request_timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("OPENFDA_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.request_timeout = request_timeout

    # -- low-level request handling -----------------------------------

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params)
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{self.base_url}{path}"

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.request_timeout)
            except requests.exceptions.RequestException as e:
                if attempt > self.max_retries:
                    raise OpenFDAError(
                        f"Network error reaching {url} after {self.max_retries} retries: {e}"
                    ) from e
                sleep_s = self.backoff_base ** attempt
                logger.warning(
                    "Network error reaching %s (%s), retrying in %.1fs (attempt %d/%d)",
                    url, e, sleep_s, attempt, self.max_retries,
                )
                time.sleep(sleep_s)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 404:
                # openFDA returns 404 with a NOT_FOUND error body when a
                # query matches zero records -- treat as an empty page.
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                if body.get("error", {}).get("code") == "NOT_FOUND":
                    return {"meta": {"results": {"total": 0}}, "results": []}
                raise OpenFDAError(f"404 from {url}: {resp.text[:500]}")

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt > self.max_retries:
                    raise OpenFDAError(
                        f"Exceeded {self.max_retries} retries for {url} "
                        f"(last status {resp.status_code}): {resp.text[:500]}"
                    )
                sleep_s = self.backoff_base ** attempt
                logger.warning(
                    "openFDA %s status %s, retrying in %.1fs (attempt %d/%d)",
                    url,
                    resp.status_code,
                    sleep_s,
                    attempt,
                    self.max_retries,
                )
                time.sleep(sleep_s)
                continue

            raise OpenFDAError(
                f"Unexpected status {resp.status_code} from {url}: {resp.text[:500]}"
            )

    # -- public API ------------------------------------------------------

    def search(
        self,
        endpoint: str,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = MAX_LIMIT,
        max_records: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Yield individual result records for `endpoint` (a key in
        ENDPOINTS, e.g. '510k'), paginating with skip/limit until either
        the result set or MAX_SKIP is exhausted.

        `search` is an openFDA Lucene-style query string, e.g.
        'product_code:"LRK"+AND+decision_date:[2015-01-01+TO+2020-12-31]'.
        Use `build_query()` to construct these from structured filters.
        """
        spec = self._spec(endpoint)
        limit = min(limit, MAX_LIMIT)
        skip = 0
        fetched = 0

        while True:
            params: Dict[str, Any] = {"limit": limit, "skip": skip}
            if search:
                params["search"] = search
            if sort:
                params["sort"] = sort

            payload = self._get(spec.path, params)
            results = payload.get("results", [])
            total = payload.get("meta", {}).get("results", {}).get("total", 0)

            for record in results:
                yield record
                fetched += 1
                if max_records is not None and fetched >= max_records:
                    return

            skip += len(results)
            if not results:
                return
            if skip >= total:
                return
            if skip > MAX_SKIP:
                logger.warning(
                    "openFDA %s: hit skip ceiling (%d) with %d/%d records "
                    "fetched for query %r; slice the query further (e.g. by "
                    "date range) to retrieve the remainder.",
                    endpoint,
                    MAX_SKIP,
                    fetched,
                    total,
                    search,
                )
                return

    def count(self, endpoint: str, search: Optional[str] = None) -> int:
        spec = self._spec(endpoint)
        params: Dict[str, Any] = {"limit": 1}
        if search:
            params["search"] = search
        payload = self._get(spec.path, params)
        return payload.get("meta", {}).get("results", {}).get("total", 0)

    @staticmethod
    def _spec(endpoint: str) -> EndpointSpec:
        try:
            return ENDPOINTS[endpoint]
        except KeyError:
            raise OpenFDAError(
                f"Unknown openFDA endpoint {endpoint!r}; known: {sorted(ENDPOINTS)}"
            )


def _quote(value: str) -> str:
    value = str(value).replace('"', '\\"')
    return f'"{value}"'


def build_query(clauses: List[str]) -> Optional[str]:
    """Join pre-built openFDA field clauses with AND. Empty clauses are dropped."""
    clauses = [c for c in clauses if c]
    if not clauses:
        return None
    return "+AND+".join(f"({c})" if "+OR+" in c else c for c in clauses)


def field_in(field_name: str, values: List[str]) -> str:
    """Build a `field:("A" OR "B" OR "C")` clause for a list of values."""
    values = [v for v in values if v]
    if not values:
        return ""
    if len(values) == 1:
        return f"{field_name}:{_quote(values[0])}"
    joined = "+OR+".join(_quote(v) for v in values)
    return f"{field_name}:({joined})"


def date_range(field_name: str, start: Optional[str], end: Optional[str]) -> str:
    """Build a `field:[start TO end]` clause. Use '*' for an open end."""
    if not start and not end:
        return ""
    start = start or "*"
    end = end or "*"
    return f"{field_name}:[{start}+TO+{end}]"
