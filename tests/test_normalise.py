from __future__ import annotations

import pytest

from core.normalise import RegionIndex, parse_education, parse_employment, parse_experience, parse_salary


@pytest.mark.parametrize("raw, expected", [
    ("Rp 5-7 jt", (5_000_000, 7_000_000, "month")),
    ("Rp4.500.000 - Rp6.000.000", (4_500_000, 6_000_000, "month")),
    ("Rp 5.000.000 - Rp 7.500.000 per bulan", (5_000_000, 7_500_000, "month")),
    ("3,5 juta", (3_500_000, 3_500_000, "month")),
    ("Rp 3 Juta", (3_000_000, 3_000_000, "month")),
    ("Gaji 4.5 juta", (4_500_000, 4_500_000, "month")),
    ("Rp 10jt - 15jt", (10_000_000, 15_000_000, "month")),
    ("IDR 7000000-9000000/month", (7_000_000, 9_000_000, "month")),
    ("IDR 8000000/month", (8_000_000, 8_000_000, "month")),
    ("Rp150rb/hari", (3_300_000, 3_300_000, "day")),
    ("IDR 150000/day", (3_300_000, 3_300_000, "day")),
    ("Rp 25.000 per jam", (4_325_000, 4_325_000, "hour")),
    ("IDR 120000000/year", (10_000_000, 10_000_000, "year")),
    ("Rp 1.500.000 per minggu", (6_500_000, 6_500_000, "week")),
])
def test_salary_is_converted_to_monthly_idr(raw, expected):
    assert parse_salary(raw) == expected


@pytest.mark.parametrize("raw", [
    "Dirahasiakan", "Negotiable", "Gaji kompetitif", "", None, "USD 2000-3000/month",
    "Rp 100.000",  # implausibly low for a month
    "Rp 500 jt",  # implausibly high
])
def test_hidden_foreign_or_implausible_salary_is_none(raw):
    assert parse_salary(raw) == (None, None, None)


@pytest.mark.parametrize("raw, expected", [
    ("Minimal SMA/SMK/Sederajat", "SMA/SMK"),
    ("high school", "SMA/SMK"),
    ("SMA / SMK / STM Diploma/D1/D2/D3 Sarjana / S1", "SMA/SMK"),
    ("Diploma/D1/D2/D3 / Sarjana / S1", "D1-D3"),
    ("associate degree", "D1-D3"),
    ("Minimal D3/D4", "D1-D3"),
    ("D3", "D1-D3"),
    ("S1 semua jurusan", "D4/S1"),
    ("bachelor degree", "D4/S1"),
    ("Sarjana (S1)", "D4/S1"),
    ("Magister (S2)", "S2"),
    ("master degree", "S2"),
    ("SMP", "SMP"),
    ("Tidak ada syarat", None),
    ("", None),
])
def test_education_is_the_lowest_level_mentioned(raw, expected):
    assert parse_education(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Fresh graduate", 0),
    ("Tanpa pengalaman", 0),
    ("Minimal 1 tahun", 1),
    ("min. 2 tahun", 2),
    ("3-5 tahun", 3),
    ("12 bulan", 1),
    ("24 bulan", 2),
    ("18 bulan", 2),
    ("3 bulan", 0),
    ("1", 1),
    ("2 years", 2),
    ("", None),
    (None, None),
])
def test_experience_is_minimum_whole_years(raw, expected):
    assert parse_experience(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("FULL_TIME", "full_time"),
    ("Full Time", "full_time"),
    ("FULL_TIME, CONTRACTOR", "full_time"),
    ("Kontrak", "contract"),
    ("Contractual", "contract"),
    ("CONTRACTOR", "contract"),
    ("Magang", "internship"),
    ("INTERN", "internship"),
    ("Part Time", "part_time"),
    ("Freelance", "freelance"),
    ("", None),
])
def test_employment_type(raw, expected):
    assert parse_employment(raw) == expected


@pytest.fixture(scope="module")
def regions():
    return RegionIndex.from_reference()


@pytest.mark.parametrize("raw, region, province", [
    ("Jaksel", "3171", "31"),
    ("Jakarta Selatan", "3171", "31"),
    ("Jakarta Timur, DKI Jakarta", "3172", "31"),
    ("Jakarta", "31", "31"),
    ("Kota Bandung", "3273", "32"),
    ("Kab. Bandung", "3204", "32"),
    ("Kabupaten Banyuwangi, Jawa Timur", "3510", "35"),
    ("Bandung, Jawa Barat", "32", "32"),     # ambiguous city -> province only
    ("Tangerang", "36", "36"),               # Kab. and Kota Tangerang are both in Banten
    ("Surabaya", "3578", "35"),
    ("Sleman", "3404", "34"),
    ("Solo", "3372", "33"),
    ("Denpasar, Bali", "5171", "51"),
    ("Kota Batam, Kepulauan Riau", "2171", "21"),
    ("Gresik, Jawa Timur", "3525", "35"),
    ("Tangerang City", "3671", "36"),             # English "City" names the kota
    ("Jawa Barat", "32", "32"),
    ("Provinsi Jawa Tengah", "33", "33"),
    ("Nowhere Land", None, None),
])
def test_location_resolves_to_bps_codes(regions, raw, region, province):
    loc = regions.resolve(raw)
    assert (loc.region_code, loc.province_code) == (region, province)


def test_remote_and_multi_location(regions):
    assert regions.resolve("Remote").is_remote
    assert regions.resolve("Remote").region_code is None
    loc = regions.resolve("Kota Medan | Kota Bandung")
    assert loc.region_code == "1275"


@pytest.mark.parametrize("raw, street, expected", [
    ("Tangerang, Banten", "Komplek 3, Cipondoh, Tangerang City, Banten 15141", "3671"),
    ("Bandung, Jawa Barat", "Jl. Rancabentang 12a, Kec. Cidadap, Kota Bandung, Jawa Barat 40142", "3273"),
    ("Bekasi, Jawa Barat", "Kawasan Industri, Cikarang, Kabupaten Bekasi", "3216"),
    ("Bekasi, Jawa Barat", "Jl. Tanpa Kota", "32"),             # nothing better: stays province
    ("Kota Medan", "Jl. Kota Bandung", "1275"),                  # already regency: untouched
])
def test_street_address_refines_province_only_matches(regions, raw, street, expected):
    assert regions.refine(regions.resolve(raw), street).region_code == expected


def test_city_and_regency_suffix(regions):
    assert regions.resolve("Bekasi Regency").region_code == "3216"
    assert regions.resolve("Bekasi City").region_code == "3275"
