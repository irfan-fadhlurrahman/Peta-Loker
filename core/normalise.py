"""Normalisation: turn what each site writes into comparable values.

Pure functions (no I/O) except RegionIndex.from_reference(), which reads the
committed reference CSVs. Every function returns None when it can't decide —
missing values stay missing; nothing is imputed.

    salary       "Rp 5-7 jt", "4.500.000", "3,5 juta", "Rp150rb/hari",
                 "IDR 7000000-9000000/month"  ->  monthly IDR (min, max)
    education    "Minimal SMA/SMK", "bachelor degree", "D3/S1"  ->  lowest level
    experience   "min. 2 tahun", "24 bulan", "Fresh graduate"   ->  whole years
    employment   "FULL_TIME", "Kontrak", "Magang"               ->  fixed list
    location     "Jaksel", "Kota Bandung", "Bandung, Jawa Barat" ->  BPS code
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

from core.text import ascii_fold, collapse_ws

REPO_ROOT = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------- salary

# Monthly equivalents: 22 working days, 52/12 weeks, 173 working hours.
TO_MONTHLY = {"month": 1.0, "day": 22.0, "week": 52 / 12, "hour": 173.0, "year": 1 / 12}
MIN_MONTHLY, MAX_MONTHLY = 500_000, 200_000_000

_NO_SALARY = re.compile(r"dirahasiakan|nego|kompetitif|competitive|confidential|tidak ditampilkan|undisclosed", re.I)
_PERIOD_PATTERNS = [
    ("hour", re.compile(r"/\s*(jam|hour)|per\s*(jam|hour)|hourly", re.I)),
    ("day", re.compile(r"/\s*(hari|day)|per\s*(hari|day)|harian|daily", re.I)),
    ("week", re.compile(r"/\s*(minggu|week)|per\s*(minggu|week)|mingguan|weekly", re.I)),
    ("year", re.compile(r"/\s*(tahun|year)|per\s*(tahun|year)|tahunan|annual|yearly", re.I)),
    ("month", re.compile(r"/\s*(bulan|month)|per\s*(bulan|month)|bulanan|monthly", re.I)),
]
# A number with an optional multiplier word right after it.
_AMOUNT = re.compile(
    r"(\d+(?:[.,]\d+)*)\s*(jt|juta|jutaan|million|mio|m\b|rb|ribu|k\b|ribuan|thousand)?",
    re.I,
)
_NON_IDR = re.compile(r"\b(usd|us\$|sgd|eur|myr|\$)", re.I)


def _to_number(token: str, multiplier: str | None) -> float | None:
    """'5.000.000' -> 5e6, '3,5' + 'juta' -> 3.5e6, '150' + 'rb' -> 1.5e5.
    Indonesian style uses '.' for thousands and ',' for decimals; a bare
    '4.5' with a multiplier is read as a decimal too."""
    if multiplier:
        value = float(token.replace(",", ".")) if token.count(".") + token.count(",") <= 1 else None
        if value is None:
            return None
        mult = multiplier.lower()
        if mult in ("jt", "juta", "jutaan", "million", "mio", "m"):
            return value * 1_000_000
        return value * 1_000
    digits = re.sub(r"[.,](?=\d{3}\b)", "", token)  # strip thousand separators
    digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def parse_salary(raw: str | None) -> tuple[int | None, int | None, str | None]:
    """(monthly_min, monthly_max, period_as_published). (None, None, None) if
    the salary is hidden, unparseable, not in rupiah, or implausible."""
    if not raw:
        return None, None, None
    text = collapse_ws(raw)
    if _NO_SALARY.search(text) or _NON_IDR.search(text):
        return None, None, None
    period = next((name for name, pattern in _PERIOD_PATTERNS if pattern.search(text)), "month")
    matches = list(_AMOUNT.finditer(text))
    # A range written "5-7 jt" carries the multiplier only on its last number:
    # bare small numbers before it borrow that multiplier.
    trailing = next((m.group(2) for m in reversed(matches) if m.group(2)), None)
    numbers = []
    for match in matches:
        token, multiplier = match.group(1), match.group(2)
        if not multiplier and trailing and _to_number(token, None) is not None and _to_number(token, None) < 1000:
            multiplier = trailing
        value = _to_number(token, multiplier)
        if value is not None:
            numbers.append(value)
    numbers = [n for n in numbers if n >= 1000]  # drop stray small numbers ("2 tahun", "08.00")
    if not numbers:
        return None, None, None
    lo, hi = min(numbers[:2]), max(numbers[:2])
    factor = TO_MONTHLY[period]
    lo_m, hi_m = int(round(lo * factor, -3)), int(round(hi * factor, -3))
    if lo_m < MIN_MONTHLY or hi_m > MAX_MONTHLY:
        return None, None, None
    return lo_m, hi_m, period


# ---------------------------------------------------------------- education

EDUCATION_LEVELS = ["SD", "SMP", "SMA/SMK", "D1-D3", "D4/S1", "S2", "S3"]
_EDUCATION_PATTERNS = [
    ("S3", re.compile(r"\bs-?3\b|doktor|doctor|phd|doctoral", re.I)),
    ("S2", re.compile(r"\bs-?2\b|magister|master|pasca\s*sarjana|postgraduate|post graduate", re.I)),
    ("D4/S1", re.compile(r"\bs-?1\b|\bd-?iv\b|\bd-?4\b|sarjana|bachelor|strata\s*1|undergraduate", re.I)),
    ("D1-D3", re.compile(r"\bd-?[123]\b|\bd-?i{1,3}\b|diploma|associate degree|akademi|politeknik", re.I)),
    ("SMA/SMK", re.compile(r"\bsma\b|\bsmk\b|\bstm\b|\bslta\b|\bma\b|sederajat|high school|secondary school|"
                           r"vocational school", re.I)),
    ("SMP", re.compile(r"\bsmp\b|\bsltp\b|\bmts\b|junior high", re.I)),
    ("SD", re.compile(r"\bsd\b|sekolah dasar|elementary|primary school", re.I)),
]


def parse_education(raw: str | None) -> str | None:
    """Lowest education level mentioned — "D3/S1" means D3 is enough."""
    if not raw:
        return None
    found = [level for level, pattern in _EDUCATION_PATTERNS if pattern.search(raw)]
    if not found:
        return None
    return min(found, key=EDUCATION_LEVELS.index)


# --------------------------------------------------------------- experience

_FRESH = re.compile(r"fresh\s*grad|tanpa pengalaman|no experience|tidak (perlu|wajib) berpengalaman|"
                    r"lulusan baru|entry level", re.I)
_YEARS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:-\s*\d+\s*)?(tahun|thn|th\b|years?|yrs?)", re.I)
_MONTHS = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?(bulan|bln|months?)", re.I)


def parse_experience(raw: str | None) -> int | None:
    """Minimum years of experience. Months are rounded to the nearest year
    (12-17 months -> 1); a bare number (KitaLulus writes "1") is years."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _FRESH.search(text):
        return 0
    if match := _YEARS.search(text):
        return int(float(match.group(1).replace(",", ".")))
    if match := _MONTHS.search(text):
        return int(math.floor(int(match.group(1)) / 12 + 0.5)) if int(match.group(1)) >= 6 else 0
    if re.fullmatch(r"\d{1,2}", text):
        return int(text)
    return None


# --------------------------------------------------------------- employment

EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "internship", "freelance"]
_EMPLOYMENT_PATTERNS = [
    ("internship", re.compile(r"intern|magang|ojt|on the job|trainee|praktik kerja|pkl", re.I)),
    ("freelance", re.compile(r"freelance|lepas|per diem|gig", re.I)),
    ("part_time", re.compile(r"part[\s_-]*time|paruh waktu|paruh-waktu", re.I)),
    ("contract", re.compile(r"contract|kontrak|temporary|temporer|pkwt|outsourc", re.I)),
    ("full_time", re.compile(r"full[\s_-]*time|penuh waktu|permanent|tetap|pkwtt|regular", re.I)),
]


def parse_employment(raw: str | None) -> str | None:
    """First recognised type in the order the source lists them."""
    if not raw:
        return None
    hits = []
    for name, pattern in _EMPLOYMENT_PATTERNS:
        match = pattern.search(raw)
        if match:
            hits.append((match.start(), name))
    return min(hits)[1] if hits else None


# ----------------------------------------------------------------- location

@dataclass(frozen=True)
class Location:
    region_code: str | None
    province_code: str | None
    is_remote: bool = False


_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|kerja dari rumah|jarak jauh|anywhere", re.I)
_PREFIX_KAB = re.compile(r"^(kab|kabupaten|regency)\b\.?\s*(adm\.?\s*)?", re.I)
_PREFIX_KOTA = re.compile(r"^(kota|city of|kodya|kotamadya)\b\.?\s*(adm\.?\s*)?", re.I)
_SUFFIX_CITY = re.compile(r"\s+(city|regency)$", re.I)
_PROVINCE_PREFIX = re.compile(r"^(provinsi|propinsi|prov\.?|province of)\s+", re.I)


def _key(text: str) -> str:
    text = ascii_fold(text).lower().replace("kep.", "kepulauan ")
    text = re.sub(r"\b\d{5}\b", " ", text)  # postal codes ("Banten 15141")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return collapse_ws(text)


class RegionIndex:
    """Lookup from free-text place names to BPS codes.

    Regencies are indexed by their full name ("kota bandung", "kabupaten
    bandung") and by their bare name ("bandung") — but a bare name that
    belongs to more than one regency (Bandung, Bekasi, Tangerang, Bogor,
    Semarang, Malang …) is marked ambiguous and resolves to the province
    only. Hand-written aliases (reference/region_aliases.csv) win."""

    def __init__(self, regions: list[dict], aliases: dict[str, str]):
        self.province_of: dict[str, str] = {}
        self.provinces: dict[str, str] = {}
        self.full: dict[str, str] = {}
        bare: dict[str, set[str]] = {}
        for r in regions:
            code = r["region_code"]
            self.province_of[code] = r["province_code"]
            name = _key(r["name"])
            if r["level"] == "province":
                self.provinces[name] = code
                self.provinces[name.replace("kepulauan ", "")] = code
                continue
            core_name = _key(_PREFIX_KOTA.sub("", _PREFIX_KAB.sub("", r["name"].lower())).replace("adm.", ""))
            kind = r.get("kind")
            prefixes = ["kota", "kota adm"] if kind == "kota" else ["kabupaten", "kab", "kab adm"]
            for prefix in prefixes:
                self.full[f"{prefix} {core_name}"] = code
            self.full[name] = code
            bare.setdefault(core_name, set()).add(code)
        self.bare = {name: next(iter(codes)) for name, codes in bare.items() if len(codes) == 1}
        self.ambiguous = {name: codes for name, codes in bare.items() if len(codes) > 1}
        self.aliases = {_key(a): code for a, code in aliases.items()}

    @classmethod
    def from_reference(cls) -> RegionIndex:
        ref = REPO_ROOT / "reference"
        with (ref / "regions.csv").open(encoding="utf-8", newline="") as f:
            regions = list(csv.DictReader(f))
        with (ref / "region_aliases.csv").open(encoding="utf-8", newline="") as f:
            aliases = {row["alias"]: row["region_code"] for row in csv.DictReader(f)}
        return cls(regions, aliases)

    def _province_code(self, code: str) -> str:
        return code if len(code) == 2 else self.province_of.get(code, code[:2])

    def _lookup(self, text: str, province_hint: str | None = None) -> str | None:
        # "Tangerang City" / "Bandung Regency" (English addresses) name the kind.
        suffix = _SUFFIX_CITY.search(text.strip())
        if suffix:
            kind = "kota" if suffix.group(1).lower() == "city" else "kabupaten"
            code = self.full.get(_key(f"{kind} {text.strip()[: suffix.start()]}"))
            if code:
                return code
        key = _key(_SUFFIX_CITY.sub("", text.strip()))
        key_noprov = _key(_PROVINCE_PREFIX.sub("", text.strip()))
        if not key:
            return None
        if key in self.aliases:
            return self.aliases[key]
        if key in self.full:
            return self.full[key]
        if key_noprov in self.provinces:
            return self.provinces[key_noprov]
        if key in self.bare:
            code = self.bare[key]
            if province_hint is None or self._province_code(code) == province_hint:
                return code
        if key in self.ambiguous:
            codes = self.ambiguous[key]
            if province_hint:
                in_province = [c for c in codes if self._province_code(c) == province_hint]
                if len(in_province) == 1:
                    return in_province[0]
                return province_hint
            provinces = {self._province_code(c) for c in codes}
            return next(iter(provinces)) if len(provinces) == 1 else None
        return None

    def resolve(self, raw: str | None) -> Location:
        """Best BPS code for a location string. Several locations
        ('A | B') -> the first one that resolves. 'City, Province' uses the
        province to disambiguate the city."""
        if not raw:
            return Location(None, None)
        if _REMOTE.search(raw) and not re.search(r"[a-z]{4,}", _REMOTE.sub("", raw.lower())):
            return Location(None, None, True)
        remote = bool(_REMOTE.search(raw))
        for part in (p.strip() for p in raw.split("|")):
            pieces = [p.strip() for p in part.split(",") if p.strip()]
            if not pieces:
                continue
            province_hint = None
            for piece in reversed(pieces[1:]):
                code = self._lookup(piece)
                if code and len(code) == 2:
                    province_hint = code
                    break
            for piece in pieces:
                code = self._lookup(piece, province_hint)
                if code:
                    return Location(code, self._province_code(code), remote)
            if province_hint:
                return Location(province_hint, province_hint, remote)
        return Location(None, None, remote)

    def refine(self, location: Location, address: str | None) -> Location:
        """If `location` is only a province, look for its regency in a street
        address ("…, Cipondoh, Tangerang City, Banten 15141")."""
        if not address or not location.region_code or len(location.region_code) != 2:
            return location
        for piece in (p.strip() for p in address.split(",")):
            code = self._lookup(piece, location.province_code)
            if code and len(code) == 4 and self._province_code(code) == location.province_code:
                return Location(code, location.province_code, location.is_remote)
        return location
