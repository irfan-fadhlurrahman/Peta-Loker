from __future__ import annotations

import pytest

from core.masking import MASK, company_hmac, find_pii, mask_text
from core.text import normalise_company, truncate


@pytest.mark.parametrize("text", [
    "Kirim CV ke hrd.rekrut@contoh-perusahaan.co.id segera",
    "WA 0812-3456-7890",
    "WA 081234567890",
    "Hubungi +62 812 3456 7890",
    "Telp 62-812-3456-789",
    "Kantor (021) 5551234",
    "Kantor 021-555-1234",
    "Chat https://wa.me/6281234567890 sekarang",
    "Join t.me/lokerjakarta",
])
def test_contact_details_are_masked(text):
    masked = mask_text(text)
    assert MASK in masked
    assert find_pii(masked) == []


@pytest.mark.parametrize("text", [
    "Gaji Rp 5.000.000 - Rp 7.500.000 per bulan",
    "Gaji 4.500.000",
    "Pengalaman minimal 2 tahun, usia 21-35 tahun",
    "Jam kerja 08.00 - 17.00 WIB",
    "Kode pos 12950",
])
def test_ordinary_numbers_are_not_masked(text):
    assert mask_text(text) == text
    assert find_pii(text) == []


def test_contact_person_names_are_masked():
    masked = mask_text("Info lebih lanjut hubungi Ibu Sari Dewi atau CP: Budi")
    assert "Sari" not in masked and "Budi" not in masked
    assert masked.count(MASK) == 2


def test_lowercase_after_cue_is_not_treated_as_a_name():
    assert mask_text("Silakan hubungi kami melalui portal") == "Silakan hubungi kami melalui portal"


def test_company_hmac_is_stable_and_ignores_legal_suffixes():
    assert company_hmac("PT. ABC Indonesia Tbk") == company_hmac("abc")
    assert company_hmac("ABC") != company_hmac("XYZ")
    assert len(company_hmac("ABC")) == 12
    assert company_hmac("") is None


def test_normalise_company_keeps_something_when_only_suffixes():
    assert normalise_company("Indonesia Group") == "indonesia group"


def test_truncate_on_word_boundary():
    assert truncate("satu dua tiga empat", 9) == "satu dua…"
    assert truncate("pendek", 50) == "pendek"
