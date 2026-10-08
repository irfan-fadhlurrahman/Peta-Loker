"""Shared fixtures. Tests never touch the network, the real database, TOS or
the LLM: the database is in-memory, the raw store is a temp dir, and HTTP
goes through httpx.MockTransport."""

from __future__ import annotations

import os

import pytest

# Set before any core module reads it (core.masking reads at call time, but
# keep the test environment explicit and independent of a developer's .env).
os.environ["HMAC_SECRET"] = "test-secret"
os.environ["RAW_STORE"] = "local"

from core import db  # noqa: E402
from core.raw_store import LocalRawStore  # noqa: E402
from scripts.db_init import load_reference  # noqa: E402


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def conn_with_reference(conn):
    load_reference(conn)
    return conn


@pytest.fixture
def raw_store(tmp_path):
    return LocalRawStore(tmp_path / "raw")


@pytest.fixture
def settings():
    """Source settings with no delays, for fast tests."""
    return {
        "user_agent": "PetaLokerBot/test",
        "max_age_days": 60,
        "delay_min": 0.0,
        "delay_max": 0.0,
        "max_pages": 300,
        "timeout_seconds": 5,
        "max_retries": 3,
        "base_url": "https://jobs.example.test",
        "enabled": True,
    }
