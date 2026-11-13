"""Configuration loading."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CRITERIA = ROOT / "config" / "criteria.toml"


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else Path(os.environ.get("PGT_CRITERIA", DEFAULT_CRITERIA))
    with open(p, "rb") as fh:
        cfg = tomllib.load(fh)
    cfg["alerts"]["site_url"] = os.environ.get("SITE_URL", cfg["alerts"].get("site_url", "")).rstrip("/")
    cfg["alerts"]["recipient"] = os.environ.get("ALERT_RECIPIENT", cfg["alerts"].get("recipient", ""))
    cfg["photos"]["archive"] = os.environ.get("PHOTO_ARCHIVE", cfg["photos"].get("archive", "off"))
    return cfg


def state_dir() -> Path:
    p = Path(os.environ.get("PGT_STATE", ROOT / "state"))
    p.mkdir(parents=True, exist_ok=True)
    return p
