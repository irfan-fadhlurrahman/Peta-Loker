"""Build reference/regions.csv: BPS province (2-digit) and regency/city
(4-digit) codes.

Source: BPS's public region lookup used by its own website
(sig.bps.go.id/rest-drop-down/getwilayah; robots.txt allows it). Without a
period parameter it still returns the old 34-province layout, so a period is
always requested — 2025_1.2025 is the latest published and has all 38
provinces, including the 2022 Papua splits. Regency names come prefixed
("KAB. BANDUNG" / "KOTA BANDUNG"), which is what keeps the two Bandungs apart.

Usage:
    uv run python scripts/build_regions.py [--period 2025_1.2025]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from core.config import collection
from core.http import HttpClient

REPO_ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://sig.bps.go.id/rest-drop-down/getwilayah"
DEFAULT_PERIOD = "2025_1.2025"
OUT_PATH = REPO_ROOT / "reference" / "regions.csv"
EXPECTED_PROVINCES = 38

logger = logging.getLogger("build_regions")


def regency_kind(name: str) -> str | None:
    upper = name.upper()
    if upper.startswith(("KOTA ", "KOTA.")):
        return "kota"
    if upper.startswith(("KAB.", "KAB ", "KABUPATEN ")):
        return "kab"
    return None


def fetch_regions(http: HttpClient, period: str) -> list[dict]:
    provinces = http.get(API_URL, params={"level": "provinsi", "periode_merge": period}).json()
    rows: list[dict] = []
    for prov in provinces:
        rows.append({"region_code": prov["kode"], "name": prov["nama"], "level": "province", "kind": None,
                     "province_code": prov["kode"]})
        regencies = http.get(
            API_URL, params={"level": "kabupaten", "parent": prov["kode"], "periode_merge": period}
        ).json()
        for reg in regencies:
            rows.append({"region_code": reg["kode"], "name": reg["nama"], "level": "regency",
                         "kind": regency_kind(reg["nama"]), "province_code": prov["kode"]})
        logger.info("%s %s: %d regencies", prov["kode"], prov["nama"], len(regencies))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    http = HttpClient.from_settings({**collection(), "delay_min": 1.0, "delay_max": 2.0})
    try:
        rows = fetch_regions(http, args.period)
    finally:
        http.close()

    provinces = [r for r in rows if r["level"] == "province"]
    regencies = [r for r in rows if r["level"] == "regency"]
    if len(provinces) != EXPECTED_PROVINCES:
        logger.error("expected %d provinces, got %d (period %s)", EXPECTED_PROVINCES, len(provinces), args.period)
        return 1
    unknown_kind = [r["name"] for r in regencies if r["kind"] is None]
    if unknown_kind:
        logger.warning("%d regencies without a KAB./KOTA prefix: %s", len(unknown_kind), unknown_kind[:10])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["region_code", "name", "level", "kind", "province_code"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    logger.info("wrote %d provinces and %d regencies to %s", len(provinces), len(regencies), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
