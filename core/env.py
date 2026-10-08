"""Loads .env into the process environment once, on first import. Any module
needing ARK_*/TOS_*/HMAC_SECRET/etc. should `import core.env` (for the side
effect) before reading os.environ, or import something that already does
(e.g. core.raw_store, core.llm_client)."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
