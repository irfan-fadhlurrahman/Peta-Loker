"""The KBJI appendix parser, on lines shaped like the real PDF (x0 = left
position in points: headings at 72, indented text at 144)."""

from __future__ import annotations

from scripts.build_kbji import parse_lines, validate
from scripts.build_regions import regency_kind

LINES = [
    (72, "0 Tentara Nasional Indonesia (TNI) dan Kepolisian Negara Republik"),
    (144, "Indonesia (Polri)"),
    (144, "Tentara Nasional Indonesia (TNI) dan Kepolisian Negara Republik"),
    (144, "Subgolongan pokok dalam golongan pokok ini diklasifikasikan sebagai berikut"),
    (144, "01 Perwira TNI dan Polri;"),
    (72, "01 Perwira TNI dan Polri"),
    (144, "Perwira TNI dan Polri memberikan kepemimpinan."),
    (284, "- 2 -"),
    (72, "0111 Perwira Markas Besar TNI"),
    (144, "Perwira Markas Besar TNI memimpin."),
    (72, "0111.01 Panglima TNI"),
    (144, "Panglima TNI memimpin TNI."),
    (72, "5151.99 Pengawas Kebersihan dan Kerumahtanggaan di Kantor, Hotel, dan"),
    (144, "Bangunan Lainnya"),
    (144, "Jabatan ini mencakup pengawas lainnya."),
]


def test_headings_only_from_left_margin_and_wrapped_titles_joined():
    rows = {r["code"]: r for r in parse_lines(LINES)}
    assert rows["0"]["title"] == "Tentara Nasional Indonesia (TNI) dan Kepolisian Negara Republik Indonesia (Polri)"
    assert rows["01"]["title"] == "Perwira TNI dan Polri"  # the indented list mention didn't win
    assert rows["0111.01"] == {"code": "0111.01", "level": 5, "title": "Panglima TNI", "parent_code": "0111"}
    assert rows["5151.99"]["title"].endswith("Hotel, dan Bangunan Lainnya")
    assert rows["0111"]["parent_code"] == "011"


def test_validate_reports_orphans():
    problems = validate(parse_lines(LINES))
    assert any("without a parent" in p for p in problems)


def test_regency_kind():
    assert regency_kind("KOTA BANDUNG") == "kota"
    assert regency_kind("KAB. BANDUNG") == "kab"
    assert regency_kind("KAB TIMOR TENGAH SELATAN") == "kab"
    assert regency_kind("KOTA ADM. JAKARTA SELATAN") == "kota"
