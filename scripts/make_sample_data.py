"""Generate data/sample/jobs.csv: synthetic job postings for `make demo`.

Everything here is invented — company names are fictional ("PT Contoh …"),
contact details are fake (example.com addresses, 0812-0000-xxxx numbers) and
exist only so the masking step has something to mask. The generator is
deterministic (fixed seed) so the committed file is reproducible.

The data is shaped to exercise every pipeline step:
  * salary written in the formats real sites use ("Rp 5-7 jt", "4.500.000",
    "3,5 juta", "Rp150rb/hari", "Dirahasiakan");
  * education/experience in free-text and structured forms;
  * locations as BPS-style names, aliases ("Jaksel") and ambiguous names;
  * ~15% reposts (same job, new id, slightly edited text) for dedup;
  * dates as `posted_days_ago`, so the demo never falls outside the 60-day
    window however long after generation it runs.

Usage:
    uv run python scripts/make_sample_data.py [--rows 600]
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "sample" / "jobs.csv"
SEED = 20261008

JOBS = [  # (title, tasks, skills)
    ("Software Engineer Backend", "membangun dan memelihara layanan API", "Python, SQL, Docker"),
    ("Frontend Developer", "mengembangkan antarmuka web yang responsif", "JavaScript, React, CSS"),
    ("Data Analyst", "mengolah data penjualan dan membuat dasbor", "SQL, Excel, Tableau"),
    ("Staff Accounting", "mencatat jurnal dan menyusun laporan keuangan bulanan", "Accurate, Excel, perpajakan"),
    ("Admin Gudang", "mencatat stok masuk dan keluar barang", "Excel, ketelitian"),
    ("Sales Executive", "mencari pelanggan baru dan mencapai target penjualan", "negosiasi, komunikasi"),
    ("Customer Service", "melayani pertanyaan dan keluhan pelanggan", "komunikasi, CRM"),
    ("Barista", "menyiapkan minuman kopi dan melayani pelanggan", "pelayanan, kebersihan"),
    ("Kasir", "melayani transaksi pembayaran di toko", "ketelitian, pelayanan"),
    ("Driver Pengiriman", "mengantar barang ke pelanggan tepat waktu", "SIM B1, mengenal rute"),
    ("Kurir Motor", "mengantar paket di area kota", "SIM C, motor pribadi"),
    ("Operator Produksi", "mengoperasikan mesin produksi sesuai SOP", "K3, kerja shift"),
    ("Teknisi Listrik", "memasang dan merawat instalasi listrik gedung", "instalasi listrik, K3"),
    ("Perawat", "memberikan asuhan keperawatan kepada pasien", "STR aktif, BTCLS"),
    ("Guru Matematika SMP", "mengajar matematika dan menyusun rencana pembelajaran", "pedagogi, matematika"),
    ("Desainer Grafis", "membuat materi visual untuk media sosial", "Figma, Adobe Illustrator"),
    ("Content Writer", "menulis artikel dan naskah konten", "copywriting, SEO"),
    ("HR Generalist", "mengelola rekrutmen dan administrasi karyawan", "rekrutmen, BPJS, payroll"),
    ("Security", "menjaga keamanan area kantor", "Gada Pratama, disiplin"),
    ("Cleaning Service", "menjaga kebersihan area kerja", "kebersihan, kerja tim"),
    ("Cook", "memasak menu restoran sesuai standar", "food safety, kerja cepat"),
    ("Marketing Manager", "menyusun strategi pemasaran dan memimpin tim", "strategi pemasaran, kepemimpinan"),
    ("Project Manager", "merencanakan dan mengawasi jalannya proyek", "manajemen proyek, komunikasi"),
    ("Mekanik Motor", "memperbaiki dan merawat sepeda motor", "mesin motor, diagnosa"),
    ("Apoteker", "mengelola obat dan memberi informasi obat", "STRA, farmasi klinis"),
    ("Tukang Las", "mengelas konstruksi besi", "las listrik, membaca gambar teknik"),
    ("Staf Administrasi", "mengelola surat dan dokumen kantor", "Microsoft Office, arsip"),
    ("Digital Marketing Specialist", "mengelola iklan digital dan media sosial", "Meta Ads, Google Ads"),
    ("Quality Control", "memeriksa mutu produk sebelum dikirim", "ISO 9001, ketelitian"),
    ("Pramuniaga", "melayani pembeli dan merapikan barang", "pelayanan, komunikasi"),
]
COMPANIES = [
    "PT Contoh Sinar Abadi", "PT Demo Teknologi Nusantara", "CV Maju Bersama Contoh", "PT Ilustrasi Pangan Sejahtera",
    "PT Sampel Logistik Cepat", "Klinik Contoh Sehat", "Sekolah Contoh Cendekia", "PT Fiktif Manufaktur Jaya",
    "Kedai Kopi Contoh", "PT Contoh Ritel Indonesia", "PT Demo Konstruksi Utama", "Bengkel Contoh Motor",
    "PT Sampel Media Kreatif", "Hotel Contoh Nusantara", "PT Contoh Farma",
]
LOCATIONS = [
    "Jakarta Selatan", "Jaksel", "Kota Bandung", "Kab. Bandung", "Surabaya", "Kota Medan", "Semarang",
    "Kota Yogyakarta", "Sleman", "Bekasi", "Tangerang", "Kota Tangerang Selatan", "Depok", "Bogor",
    "Denpasar, Bali", "Makassar", "Kota Batam", "Palembang", "Balikpapan", "Malang", "Solo", "Remote",
]
SALARY_FORMATS = [
    lambda lo, hi: f"Rp {lo // 1_000_000}-{hi // 1_000_000} jt",
    lambda lo, hi: f"Rp{lo:,.0f} - Rp{hi:,.0f}".replace(",", "."),
    lambda lo, hi: f"{lo / 1_000_000:.1f} juta".replace(".", ","),
    lambda lo, hi: f"Rp{lo // 22 // 1000}rb/hari",
    lambda lo, hi: "Dirahasiakan",
    lambda lo, hi: "Negotiable",
    lambda lo, hi: "",
]
EDUCATION = ["SMA/SMK", "Minimal SMA/SMK/Sederajat", "D3", "Diploma/D1/D2/D3", "S1", "Sarjana / S1",
             "S1 semua jurusan", "SMP", "bachelor degree", ""]
EXPERIENCE = ["Fresh graduate", "Minimal 1 tahun", "min. 2 tahun", "3-5 tahun", "12 bulan", "", "Tanpa pengalaman"]
EMPLOYMENT = ["Full Time", "FULL_TIME", "Kontrak", "Part Time", "Magang", "Freelance"]
LEVELS = ["Staff / Officer", "Supervisor / Koordinator", "Manager", "Entry level", ""]


def _salary(rng: random.Random, title: str) -> tuple[int, int]:
    base = 4_000_000
    if any(w in title for w in ("Manager", "Engineer", "Apoteker", "Developer")):
        base = 9_000_000
    elif any(w in title for w in ("Barista", "Kasir", "Cleaning", "Security", "Pramuniaga", "Kurir")):
        base = 3_200_000
    lo = int(base * rng.uniform(0.85, 1.2) // 100_000 * 100_000)
    return lo, int(lo * rng.uniform(1.15, 1.5) // 100_000 * 100_000)


def _description(rng: random.Random, title: str, tasks: str, skills: str, company: str) -> str:
    contact = rng.choice([
        f"Kirim CV ke rekrutmen{rng.randint(1, 99)}@example.com",
        f"Hubungi Ibu Contoh via WA 0812-0000-{rng.randint(1000, 9999)}",
        f"Lamar melalui https://wa.me/62812000{rng.randint(10000, 99999)}",
        "Lamar langsung melalui tombol apply.",
    ])
    return (f"{company} membuka lowongan {title}. Tanggung jawab: {tasks}. "
            f"Kualifikasi: menguasai {skills}; mampu bekerja dalam tim; teliti dan bertanggung jawab. {contact}")


def generate(rows: int, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []
    n = 0
    while len(out) < rows:
        title, tasks, skills = rng.choice(JOBS)
        company = rng.choice(COMPANIES)
        lo, hi = _salary(rng, title)
        n += 1
        days = rng.randint(0, 58)
        fmt = rng.choice(SALARY_FORMATS)
        row = {
            "source_job_id": f"S{n:05d}",
            "url": "",
            "title": title,
            "company_name": company,
            "location_raw": rng.choice(LOCATIONS),
            "salary_raw": fmt(lo, hi),
            "salary_min": "",
            "salary_max": "",
            "salary_period": "",
            "education_raw": rng.choice(EDUCATION),
            "experience_raw": rng.choice(EXPERIENCE),
            "employment_type": rng.choice(EMPLOYMENT),
            "description": _description(rng, title, tasks, skills, company),
            "posted_days_ago": days,
            "valid_days_ahead": rng.randint(7, 45),
            "extra_job_level": rng.choice(LEVELS),
        }
        out.append(row)
        if rng.random() < 0.15 and len(out) < rows:  # a repost: new id, same job, lightly edited
            n += 1
            repost = dict(row, source_job_id=f"S{n:05d}", posted_days_ago=max(0, days - rng.randint(1, 10)))
            repost["description"] = row["description"].replace("membuka lowongan", "kembali membuka lowongan")
            out.append(repost)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)
    rows = generate(args.rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} synthetic postings to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
