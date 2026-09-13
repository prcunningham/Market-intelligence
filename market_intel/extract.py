"""
Dotted-path extraction over openFDA's nested JSON records.

openFDA device records mix plain fields, nested objects (`openfda: {...}`),
and arrays of objects (`device: [...]`, `product_codes: [...]`). A single
dotted path like "device.openfda.product_code" may need to fan out across
an array at any segment and collect every match -- this is the shared
utility every endpoint's field mapping in `endpoints.py` relies on.
"""

from __future__ import annotations

from typing import Any, List, Optional


def extract_values(record: Any, dotted_path: str) -> List[Any]:
    """Return every value found at `dotted_path`, flattening through any
    arrays encountered along the way. Missing keys yield an empty list."""
    parts = dotted_path.split(".")
    frontier = [record]
    for part in parts:
        next_frontier: List[Any] = []
        for node in frontier:
            if isinstance(node, list):
                for item in node:
                    if isinstance(item, dict) and part in item:
                        next_frontier.append(item[part])
            elif isinstance(node, dict) and part in node:
                next_frontier.append(node[part])
        frontier = next_frontier
        if not frontier:
            return []

    flat: List[Any] = []
    for value in frontier:
        if isinstance(value, list):
            flat.extend(v for v in value if v is not None)
        elif value is not None:
            flat.append(value)
    return flat


def extract_first(record: Any, dotted_path: Optional[str]) -> Optional[str]:
    if not dotted_path:
        return None
    values = extract_values(record, dotted_path)
    return str(values[0]) if values else None


def extract_id(record: Any, id_fields: List[str]) -> str:
    """Join one or more (usually top-level) fields into a stable record id."""
    parts = []
    for f in id_fields:
        v = extract_first(record, f)
        if v:
            parts.append(v)
    return "-".join(parts) if parts else ""
