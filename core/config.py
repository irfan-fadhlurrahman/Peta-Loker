"""Loads config/sources.yaml once per process, with small typed accessors so
callers don't reach into the raw dict shape."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "sources.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def collection() -> dict:
    """Global collection defaults: user_agent, max_age_days, delay range,
    timeouts. Per-source values in sources() override these."""
    return load_config()["collection"]


def sources() -> dict[str, dict]:
    """{source_name: settings} for every configured source, enabled or not."""
    return load_config()["sources"]


def source(name: str) -> dict:
    """One source's settings merged over the collection defaults."""
    try:
        own = sources()[name]
    except KeyError:
        raise KeyError(f"unknown source {name!r}; configured: {sorted(sources())}") from None
    return {**collection(), **own}


def enabled_sources() -> list[str]:
    return [name for name, cfg in sources().items() if cfg.get("enabled", True)]


def dedup() -> dict:
    return load_config()["dedup"]


def llm() -> dict:
    return load_config()["llm"]


def masking() -> dict:
    return load_config()["masking"]


def quality() -> dict:
    return load_config()["quality"]


def public() -> dict:
    return load_config()["public"]
