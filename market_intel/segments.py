"""
Segment definition layer.

A "segment" is a saved, reusable definition of a competitive slice --
mirroring how the prior sleep-appliance project defined its
LRK/LQZ/PLC product-code basket -- but generalized so a segment can be
scoped by any combination of:

  product_codes    e.g. ["LRK", "LQZ", "PLC"]
  cfr_numbers      e.g. ["868.5905"]
  device_classes   e.g. ["2"]
  applicants       raw company name(s) as they'd appear in openFDA
                    (pre-normalization -- useful for "everything from
                    this filer" segments)
  date_start / date_end   ISO dates (YYYY-MM-DD), applied to each
                    endpoint's primary date field where it has one
  free_text        an openFDA query fragment appended as-is, for
                    anything the structured filters don't cover
  endpoints        which device endpoints to pull (defaults to
                    DEFAULT_ENDPOINTS)

Segments are stored as YAML files under config/segments/, one file per
segment, so new engagements can add a segment by dropping in a new file
rather than writing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .paths import SEGMENTS_DIR
from .sources.openfda import DEFAULT_ENDPOINTS, ENDPOINTS
from .sources.openfda.client import build_query, date_range, field_in


@dataclass
class Segment:
    name: str
    description: str = ""
    product_codes: List[str] = field(default_factory=list)
    cfr_numbers: List[str] = field(default_factory=list)
    device_classes: List[str] = field(default_factory=list)
    applicants: List[str] = field(default_factory=list)
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    free_text: Optional[str] = None
    endpoints: List[str] = field(default_factory=lambda: list(DEFAULT_ENDPOINTS))

    @classmethod
    def from_dict(cls, name: str, d: Dict) -> "Segment":
        return cls(
            name=name,
            description=d.get("description", ""),
            product_codes=list(d.get("product_codes", [])),
            cfr_numbers=list(d.get("cfr_numbers", [])),
            device_classes=[str(c) for c in d.get("device_classes", [])],
            applicants=list(d.get("applicants", [])),
            date_start=d.get("date_start"),
            date_end=d.get("date_end"),
            free_text=d.get("free_text"),
            endpoints=list(d.get("endpoints", DEFAULT_ENDPOINTS)),
        )

    @classmethod
    def load(cls, name: str, segments_dir: Path = SEGMENTS_DIR) -> "Segment":
        path = segments_dir / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No segment definition at {path}")
        data = yaml.safe_load(path.read_text()) or {}
        return cls.from_dict(name, data)

    def as_dict(self) -> Dict:
        return {
            "description": self.description,
            "product_codes": self.product_codes,
            "cfr_numbers": self.cfr_numbers,
            "device_classes": self.device_classes,
            "applicants": self.applicants,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "free_text": self.free_text,
            "endpoints": self.endpoints,
        }

    def save(self, segments_dir: Path = SEGMENTS_DIR) -> Path:
        segments_dir.mkdir(parents=True, exist_ok=True)
        path = segments_dir / f"{self.name}.yaml"
        path.write_text(yaml.safe_dump(self.as_dict(), sort_keys=False))
        return path

    def query_for_endpoint(self, endpoint_key: str) -> Optional[str]:
        """Build the openFDA `search=` string for this segment against one endpoint."""
        spec = ENDPOINTS[endpoint_key]
        clauses = [field_in(spec.product_code_search_field, self.product_codes)]

        if self.cfr_numbers and spec.regulation_number_field:
            clauses.append(field_in(spec.regulation_number_field, self.cfr_numbers))
        if self.device_classes and spec.device_class_field:
            clauses.append(field_in(spec.device_class_field, self.device_classes))
        if self.applicants and spec.company_field:
            clauses.append(field_in(spec.company_field, self.applicants))
        if (self.date_start or self.date_end) and spec.date_field:
            clauses.append(date_range(spec.date_field, self.date_start, self.date_end))
        if self.free_text:
            clauses.append(self.free_text)

        return build_query(clauses)

    def is_empty_for_endpoint(self, endpoint_key: str) -> bool:
        """True if this segment has no filter that applies to this endpoint
        at all (e.g. a device_class-only segment against `udi`, which has no
        device_class_field) -- pulling with no filter would mean "everything
        openFDA has," which is never what a segment intends."""
        return self.query_for_endpoint(endpoint_key) is None


def list_segments(segments_dir: Path = SEGMENTS_DIR) -> List[str]:
    if not segments_dir.exists():
        return []
    return sorted(p.stem for p in segments_dir.glob("*.yaml"))
