"""Raw page store: the untouched bytes of every fetched page, so parsers can be
fixed and re-run (`make reparse`) without fetching a site again.

Key layout (same for both backends):
    job_market/<source>/<YYYY-MM-DD>/<safe_id>.html.gz    gzipped page body
    job_market/<source>/<YYYY-MM-DD>/<safe_id>.meta.json  url, fetched_at, status, sha256

Backends, chosen by RAW_STORE (env):
    tos    BytePlus TOS (Torch Object Storage) — the real runs. Credentials:
           TOS_ACCESS_KEY, TOS_SECRET_KEY, TOS_ENDPOINT, TOS_REGION,
           TOS_BUCKET_NAME. The bucket must be private (raw pages contain
           contact details) with a ~90-day lifecycle rule.
    local  files under data/raw/ (gitignored) — `make demo`, tests, and any
           machine without TOS credentials.
If RAW_STORE is unset, tos is used when TOS credentials exist, else local.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

import core.env  # noqa: F401  (import for side effect: loads .env)
from core.timeutil import now_iso, today_jakarta

logger = logging.getLogger(__name__)

KEY_PREFIX = "job_market"
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_ROOT = REPO_ROOT / "data" / "raw"


def _safe_id(raw_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw_id)
    return safe[:120] or hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:16]


def page_key(source: str, raw_id: str, day: str | None = None) -> str:
    """Key of a page body; its metadata lives next to it with .meta.json."""
    day = day or today_jakarta().isoformat()
    return f"{KEY_PREFIX}/{source}/{day}/{_safe_id(raw_id)}.html.gz"


def meta_key(key: str) -> str:
    return key.removesuffix(".html.gz") + ".meta.json"


class RawStore(ABC):
    name: str

    def save(self, source: str, raw_id: str, body: str, meta: dict) -> str:
        """Store one page (gzipped) plus its metadata. Returns the page key."""
        key = page_key(source, raw_id)
        data = body.encode("utf-8")
        meta = {
            **meta,
            "source": source,
            "raw_id": raw_id,
            "saved_at": now_iso(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        self._put(key, gzip.compress(data), "application/gzip")
        self._put(meta_key(key), json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
        return key

    def load(self, key: str) -> str:
        return gzip.decompress(self._get(key)).decode("utf-8")

    def load_meta(self, key: str) -> dict:
        return json.loads(self._get(meta_key(key)).decode("utf-8"))

    def list_pages(self, source: str, day: str | None = None) -> list[str]:
        """Page keys for a source (optionally one day), sorted."""
        prefix = f"{KEY_PREFIX}/{source}/" + (f"{day}/" if day else "")
        return sorted(k for k in self._list(prefix) if k.endswith(".html.gz"))

    @abstractmethod
    def _put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def _get(self, key: str) -> bytes: ...

    @abstractmethod
    def _list(self, prefix: str) -> list[str]: ...


class LocalRawStore(RawStore):
    name = "local"

    def __init__(self, root: Path | str = LOCAL_ROOT):
        self.root = Path(root)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def _list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return [p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()]


class TosRawStore(RawStore):
    name = "tos"

    def __init__(self):
        import tos  # imported lazily: the local backend must work without the SDK configured

        # Tight timeouts, as in the social-media pipeline: the SDK's defaults
        # were observed to stall a whole run on one hung upload.
        self._client = tos.TosClientV2(
            ak=os.environ["TOS_ACCESS_KEY"],
            sk=os.environ["TOS_SECRET_KEY"],
            endpoint=os.environ["TOS_ENDPOINT"],
            region=os.environ["TOS_REGION"],
            connection_time=10,
            socket_timeout=30,
            request_timeout=30,
            max_retry_count=2,
        )
        self.bucket = os.environ["TOS_BUCKET_NAME"]

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(self.bucket, key, content=data, content_type=content_type)

    def _get(self, key: str) -> bytes:
        return self._client.get_object(self.bucket, key).read()

    def _list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            result = self._client.list_objects_type2(
                self.bucket, prefix=prefix, max_keys=1000, continuation_token=token
            )
            keys.extend(obj.key for obj in result.contents)
            if not result.is_truncated:
                return keys
            token = result.next_continuation_token


def _tos_configured() -> bool:
    return all(os.environ.get(v) for v in
               ("TOS_ACCESS_KEY", "TOS_SECRET_KEY", "TOS_ENDPOINT", "TOS_REGION", "TOS_BUCKET_NAME"))


@lru_cache(maxsize=1)
def get_raw_store() -> RawStore:
    choice = (os.environ.get("RAW_STORE") or "").strip().lower()
    if choice == "local" or (not choice and not _tos_configured()):
        if not choice:
            logger.info("RAW_STORE unset and no TOS credentials: using local raw store at %s", LOCAL_ROOT)
        return LocalRawStore()
    if choice not in ("", "tos"):
        raise ValueError(f"RAW_STORE must be 'tos' or 'local', got {choice!r}")
    if not _tos_configured():
        raise RuntimeError("RAW_STORE=tos but TOS_* variables are missing (see .env.example)")
    return TosRawStore()
