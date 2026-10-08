"""Build reference/kbji_2026.csv from the official KBJI 2026 text.

Source: Peraturan Badan Pusat Statistik Nomor 7 Tahun 2026 tentang
Klasifikasi Baku Jabatan Indonesia (ditetapkan 8 September 2026), Lampiran —
published as a PDF on peraturan.go.id. The appendix lists every code with its
title and a description:

    0 Tentara Nasional Indonesia (TNI) dan Kepolisian Negara Republik    <- level 1, title wraps
        Indonesia (Polri)
        Tentara Nasional Indonesia (TNI) dan ... mencakup ...           <- description restates the title
    0111 Perwira Markas Besar TNI                                      <- level 4 (subgolongan)
    0111.01 Panglima TNI                                               <- level 5 (jabatan)

How headings are told apart from the many codes quoted inside descriptions
("... diklasifikasikan sebagai berikut: 0111 perwira Mabes TNI; ..."): a real
heading starts at the left margin of the page; quotes are indented. A title
that wraps is detected because the description that follows starts by
repeating the title.

Usage:
    uv run python scripts/build_kbji.py                 # downloads the PDF (cached under data/cache/)
    uv run python scripts/build_kbji.py --pdf file.pdf  # use a local copy
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_URL = "https://peraturan.go.id/files/peraturan-bps-no-7-tahun-2026.pdf"
CACHE_PATH = REPO_ROOT / "data" / "cache" / "peraturan-bps-no-7-tahun-2026.pdf"
OUT_PATH = REPO_ROOT / "reference" / "kbji_2026.csv"

# Totals in the published appendix. Jabatan matches the 2,380 BPS announced.
# Press coverage at launch said 449 subgolongan, but the appendix has 447
# subgolongan headings, each with jabatan under it. The extra two in the
# coverage look like two typos in the appendix's own summary lists, which name
# "2613 profesional hukum YTDL" and "6124 pekerja peternakan YTDL" while the
# headings and their jabatan use 2619 and 6129.
EXPECTED = {1: 10, 2: 43, 3: 130, 4: 447, 5: 2380}
HEADING = re.compile(r"^(\d{1,4}|\d{4}\.\d{2})\s+(\S.*)$")
PAGE_NUMBER = re.compile(r"^-\s*\d+\s*-$")
WRAP_CONNECTORS = {"dan", "atau", "serta", "di", "yang", "untuk", "dengan", "pada", "dari"}
LEFT_MARGIN_MAX_X = 100  # headings sit at x≈72pt; quoted codes and body text at x≈144pt
FIRST_APPENDIX_PAGE = 5  # pages 1-4 are the regulation's articles

logger = logging.getLogger("build_kbji")


def level_of(code: str) -> int:
    digits = code.replace(".", "")
    return 5 if "." in code else len(digits)


def parent_of(code: str) -> str | None:
    if "." in code:
        return code.split(".")[0]
    return code[:-1] or None


def _restates(line: str, title: str) -> bool:
    """True if `line` begins with the first words of `title` (the
    description's opening sentence)."""
    words = title.split()
    prefix = " ".join(words[: min(3, len(words))]).lower()
    return bool(prefix) and line.lower().startswith(prefix)


def parse_lines(lines: list[tuple[float, str]]) -> list[dict]:
    """lines: (x0, text) for every text line of the appendix, in reading
    order. Returns [{code, level, title, parent_code}] for every heading,
    one row per code (the first heading wins)."""
    lines = [(x0, text) for x0, text in lines if not PAGE_NUMBER.match(text.strip())]
    rows: dict[str, dict] = {}
    for i, (x0, text) in enumerate(lines):
        text = text.strip()
        match = HEADING.match(text)
        if not match or x0 > LEFT_MARGIN_MAX_X:
            continue
        code, title = match.group(1), match.group(2).strip()
        if code in rows:
            continue
        # Wrapped title: look up to 3 lines ahead for the description that
        # repeats the title; anything in between is the rest of the title.
        restated_at = None
        for k in range(1, min(4, len(lines) - i)):
            nxt_x0, nxt = lines[i + k][0], lines[i + k][1].strip()
            if HEADING.match(nxt) and nxt_x0 <= LEFT_MARGIN_MAX_X:
                break
            if _restates(nxt, title):
                restated_at = k
                break
        if restated_at:
            title = " ".join([title, *(lines[i + j][1].strip() for j in range(1, restated_at))])
        else:
            # No restatement (e.g. "Jabatan ini mencakup ..."). Only an
            # unbalanced parenthesis or a dangling connector word ("... di
            # Kantor, Hotel, dan") is trusted as evidence of a wrap.
            dangling = title.split()[-1].lower() in WRAP_CONNECTORS or title.endswith(",")
            if (dangling or title.count("(") > title.count(")")) and i + 1 < len(lines):
                title = f"{title} {lines[i + 1][1].strip()}"
        rows[code] = {"code": code, "level": level_of(code), "title": title, "parent_code": parent_of(code)}
    return sorted(rows.values(), key=lambda r: (r["code"].replace(".", ""), r["level"]))


def validate(rows: list[dict]) -> list[str]:
    """Problems found (empty = good): totals that don't match BPS's official
    counts, and codes whose parent is missing."""
    problems = []
    by_level = Counter(r["level"] for r in rows)
    for level, expected in EXPECTED.items():
        if by_level[level] != expected:
            problems.append(f"level {level}: {by_level[level]} codes, expected {expected}")
    codes = {r["code"] for r in rows}
    orphans = [r["code"] for r in rows if r["parent_code"] and r["parent_code"] not in codes]
    if orphans:
        problems.append(f"{len(orphans)} codes without a parent, e.g. {orphans[:5]}")
    return problems


def read_pdf_lines(pdf_path: Path) -> list[tuple[float, str]]:
    import pdfplumber  # dev dependency: only needed to rebuild the reference file

    lines: list[tuple[float, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[FIRST_APPENDIX_PAGE - 1:]:
            for line in page.extract_text_lines():
                lines.append((line["x0"], line["text"]))
    return lines


def download_pdf(dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s", PDF_URL)
    with httpx.stream("GET", PDF_URL, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", type=Path, help="local copy of the regulation PDF")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    pdf_path = args.pdf or download_pdf(CACHE_PATH)
    rows = parse_lines(read_pdf_lines(pdf_path))
    problems = validate(rows)
    for problem in problems:
        logger.error(problem)
    if problems:
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "level", "title", "parent_code"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(r["level"] for r in rows)
    logger.info("wrote %d codes to %s (%s)", len(rows), args.out, dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
