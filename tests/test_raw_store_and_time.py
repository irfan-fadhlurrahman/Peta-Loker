from __future__ import annotations

from datetime import date, datetime

from core.raw_store import meta_key, page_key
from core.timeutil import JAKARTA, days_ago, from_epoch, to_iso


def test_local_raw_store_round_trip(raw_store):
    key = raw_store.save("glints", "abc/123?x=1", "<html>halo</html>", {"url": "https://glints.com/x"})
    assert key.startswith("job_market/glints/") and key.endswith(".html.gz")
    assert "/" not in key.rsplit("/", 1)[-1].removesuffix(".html.gz")
    assert raw_store.load(key) == "<html>halo</html>"
    meta = raw_store.load_meta(key)
    assert meta["url"] == "https://glints.com/x" and meta["bytes"] == len("<html>halo</html>")
    assert raw_store.list_pages("glints") == [key]


def test_keys_follow_the_documented_layout():
    key = page_key("dealls", "job-1", day="2026-10-08")
    assert key == "job_market/dealls/2026-10-08/job-1.html.gz"
    assert meta_key(key) == "job_market/dealls/2026-10-08/job-1.meta.json"


def test_to_iso_keeps_dates_and_converts_to_jakarta():
    assert to_iso("2026-10-01") == "2026-10-01"
    assert to_iso("2026-10-01T00:00:00Z") == "2026-10-01T07:00:00+07:00"
    assert to_iso(datetime(2026, 10, 1, 9, 0)) == "2026-10-01T09:00:00+07:00"
    assert to_iso("not a date") is None
    assert to_iso(None) is None


def test_from_epoch_and_days_ago():
    assert from_epoch(0) == datetime(1970, 1, 1, 7, 0, tzinfo=JAKARTA).isoformat()
    assert days_ago("2026-09-08", today=date(2026, 10, 8)) == 30
    assert days_ago("2026-10-08T23:00:00+07:00", today=date(2026, 10, 8)) == 0
    assert days_ago(None) is None
