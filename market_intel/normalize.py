"""
Company-name normalization and category tagging.

This generalizes the manual "map subsidiary/historical names to the current
parent" approach used in the earlier sleep-appliance 510(k) project (e.g.
Respironics -> Philips, Restore Medical / Pi Medical -> Medtronic) into a
maintainable, config-driven lookup rather than per-project hardcoding.

Two independent YAML files, both hand-maintained:

  config/company_aliases.yaml
      A list of {canonical, aliases: [...]} entries. Any raw applicant /
      recalling_firm / manufacturer name seen in openFDA data that matches
      an alias (after cleaning: case/punctuation/legal-suffix insensitive)
      is mapped to `canonical`. Small or individual applicants that have no
      entry are left as their cleaned raw name -- they are not forced into
      a mapping that doesn't exist.

  config/company_categories.yaml
      A flat mapping of canonical company name -> category tag (e.g.
      "Strategic/Incumbent Medtech", "PE-Backed Platform",
      "Single-Product Startup", "CDMO/Private-Label Filer"). openFDA has no
      such concept natively, so this is entirely analyst-maintained.
      Companies with no entry are tagged "Unclassified" rather than guessed.

To add a new mapping or category tag: edit the YAML files directly (see the
comments at the top of each for the expected shape) and re-run normalization
-- there is no code change required. `unmapped_companies()` on a
CompanyNormalizer surfaces every raw name that fell through to "unmapped" or
"Unclassified" during a run, as a starting list for what to triage next.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from .paths import COMPANY_ALIASES_PATH, COMPANY_CATEGORIES_PATH

_LEGAL_SUFFIXES = [
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "llc", "l l c", "l.l.c", "lp", "l.p", "llp",
    "plc", "gmbh", "ag", "sa", "s.a", "nv", "bv", "kg", "kgaa",
    "pty", "pty ltd", "spa", "s.p.a", "srl", "s.r.l", "ab", "as",
    "holdings", "holding", "group", "international", "intl", "usa",
    "us", "of america", "north america",
]
# Longest first so multi-word suffixes match before their trailing word does.
_LEGAL_SUFFIXES.sort(key=len, reverse=True)

_PUNCT_RE = re.compile(r"[.,'`\"]")
_WS_RE = re.compile(r"\s+")


def clean_name(raw: str) -> str:
    """Normalize case/punctuation/legal-suffixes so name variants collide.

    'Philips Respironics, Inc.' / 'PHILIPS RESPIRONICS INC' /
    'Philips Respironics Incorporated' all clean to 'philips respironics'.
    """
    if not raw:
        return ""
    name = raw.strip().lower()
    name = _PUNCT_RE.sub("", name)
    name = re.sub(r"[&/]", " ", name)
    name = _WS_RE.sub(" ", name).strip()

    changed = True
    while changed:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            pattern = rf"(^|\s){re.escape(suffix)}$"
            new_name = re.sub(pattern, "", name).strip()
            if new_name != name:
                name = new_name
                changed = True
    return name


def _title_case(cleaned: str) -> str:
    """Best-effort human-readable fallback for names with no alias entry."""
    return " ".join(w.upper() if len(w) <= 3 and w.isalpha() else w.capitalize()
                     for w in cleaned.split())


@dataclass
class CompanyNormalizer:
    aliases_path: Path = COMPANY_ALIASES_PATH
    categories_path: Path = COMPANY_CATEGORIES_PATH
    _alias_lookup: Dict[str, str] = field(default_factory=dict, repr=False)
    _categories: Dict[str, str] = field(default_factory=dict, repr=False)
    _unmapped: Set[str] = field(default_factory=set, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._alias_lookup = self._load_aliases(self.aliases_path)
            self._categories = self._load_categories(self.categories_path)
            self._unmapped = set()

    @staticmethod
    def _load_aliases(path: Path) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        if not path.exists():
            return lookup
        data = yaml.safe_load(path.read_text()) or []
        entries = data.get("companies", data) if isinstance(data, dict) else data
        for entry in entries or []:
            canonical = entry["canonical"]
            lookup[clean_name(canonical)] = canonical
            for alias in entry.get("aliases", []):
                lookup[clean_name(alias)] = canonical
        return lookup

    @staticmethod
    def _load_categories(path: Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        categories = data.get("categories", data) if isinstance(data, dict) else {}
        # Keyed by cleaned canonical name so lookups don't depend on exact casing.
        return {clean_name(k): v for k, v in categories.items()}

    def normalize(self, raw_name: Optional[str]) -> Optional[str]:
        """Return the canonical company name for a raw applicant/firm name.

        Falls back to a title-cased version of the cleaned raw name when no
        alias entry exists, and records the raw name as unmapped so callers
        can surface it for triage.
        """
        if not raw_name or not raw_name.strip():
            return None
        cleaned = clean_name(raw_name)
        if not cleaned:
            return raw_name.strip()
        canonical = self._alias_lookup.get(cleaned)
        if canonical:
            return canonical
        self._unmapped.add(raw_name.strip())
        return _title_case(cleaned)

    def category(self, canonical_name: Optional[str]) -> str:
        if not canonical_name:
            return "Unclassified"
        return self._categories.get(clean_name(canonical_name), "Unclassified")

    def unmapped_companies(self) -> List[str]:
        """Raw names seen since the last reload() that had no alias entry.

        These aren't necessarily wrong -- most will be small/individual
        applicants with no parent to map to -- but they're a good worklist
        for deciding what (if anything) to add to company_aliases.yaml.
        """
        return sorted(self._unmapped)


_default_normalizer: Optional[CompanyNormalizer] = None


def get_default_normalizer() -> CompanyNormalizer:
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = CompanyNormalizer()
    return _default_normalizer
