"""Central place for on-disk locations, so every layer agrees on where
config and data live without hardcoding paths repeatedly."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("MARKET_INTEL_CONFIG_DIR", REPO_ROOT / "config"))
SEGMENTS_DIR = CONFIG_DIR / "segments"
COMPANY_ALIASES_PATH = CONFIG_DIR / "company_aliases.yaml"
COMPANY_CATEGORIES_PATH = CONFIG_DIR / "company_categories.yaml"

DATA_DIR = Path(os.environ.get("MARKET_INTEL_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("MARKET_INTEL_DB_PATH", DATA_DIR / "market_intel.sqlite"))
