"""Hand-label the review sheets in the terminal instead of a spreadsheet.

    kbji   data/review/kbji_labels.csv (from `evaluate.py --sample N`):
           Enter accepts the predicted code, a code (4 or 6 digits, "2431" or
           "2431.04") replaces it — checked against KBJI 2026 — "?" looks up
           codes by word, "s" skips, "q" quits.
    dedup  data/review/dedup_pairs.csv (from `dedup.py --review N`):
           "y" same job, "n" different job, "s" skips, "q" quits.

Rows that already have a label are skipped, and the sheet is saved after
every answer, so a session can be stopped and resumed at any time. Then:

    uv run python scripts/evaluate.py --score data/review/kbji_labels.csv

Usage:
    uv run python scripts/label.py kbji|dedup [--sheet PATH]
"""

from __future__ import annotations

import argparse
import csv
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEETS = {"kbji": REPO_ROOT / "data" / "review" / "kbji_labels.csv",
          "dedup": REPO_ROOT / "data" / "review" / "dedup_pairs.csv"}
LABEL_COLUMN = {"kbji": "gold_kbji_code", "dedup": "label"}


def load(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def save(path: Path, fields: list[str], rows: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def kbji_titles() -> dict[str, str]:
    with (REPO_ROOT / "reference" / "kbji_2026.csv").open(encoding="utf-8", newline="") as f:
        return {row["code"]: row["title"] for row in csv.DictReader(f) if row["level"] in ("4", "5")}


def normalise_code(answer: str, titles: dict[str, str]) -> str | None:
    """'243104' / '2431.04' / '2431' -> the code as stored, or None if unknown."""
    digits = answer.replace(".", "").strip()
    code = f"{digits[:4]}.{digits[4:]}" if len(digits) == 6 else digits
    return code if code in titles else None


def ask_kbji(row: dict, titles: dict[str, str]) -> str | None:
    print(f"\n{row['title']}   [{row['source']}]")
    if row.get("level_function"):
        print(f"  level/function: {row['level_function']}")
    print(textwrap.indent(textwrap.fill((row.get("description") or "")[:400], 100), "  "))
    print(f"  predicted: {row['predicted_kbji_code']} {row['predicted_title']} "
          f"(confidence {row['confidence']}; second choice {row.get('alt4') or '-'})")
    while True:
        answer = input("  Enter = correct | code | ? word | s skip | q quit > ").strip()
        if answer == "":
            return row["predicted_kbji_code"]
        if answer.lower() in ("s", "q"):
            return answer.lower()
        if answer.startswith("?"):
            word = answer[1:].strip().lower()
            hits = [(c, t) for c, t in titles.items() if word and word in t.lower()][:15]
            print("\n".join(f"    {c}  {t}" for c, t in hits) or "    no match")
            continue
        code = normalise_code(answer, titles)
        if code:
            print(f"  -> {code} {titles[code]}")
            return code
        print("  not a KBJI 2026 unit group or jabatan code")


def ask_dedup(row: dict) -> str | None:
    print(f"\npair {row['pair_id']} (dedup said {'same' if row['predicted'] == '1' else 'different'})")
    for side in ("a", "b"):
        print(f"  {side.upper()}: {row[f'{side}_title']}  [region {row[f'{side}_region']}]")
        print(textwrap.indent(textwrap.fill((row.get(f"{side}_text") or "")[:300], 100), "     "))
    while True:
        answer = input("  y same job | n different | s skip | q quit > ").strip().lower()
        if answer in ("y", "n"):
            return "1" if answer == "y" else "0"
        if answer in ("s", "q"):
            return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sheet_type", choices=sorted(SHEETS))
    parser.add_argument("--sheet", type=Path)
    args = parser.parse_args(argv)
    path = args.sheet or SHEETS[args.sheet_type]
    if not path.exists():
        print(f"{path} not found — create it first (see this script's docstring)")
        return 1
    fields, rows = load(path)
    column = LABEL_COLUMN[args.sheet_type]
    titles = kbji_titles() if args.sheet_type == "kbji" else {}
    todo = [r for r in rows if not (r.get(column) or "").strip()]
    print(f"{len(rows) - len(todo)} of {len(rows)} already labelled; {len(todo)} to go")
    for i, row in enumerate(todo, 1):
        print(f"\n--- {i}/{len(todo)}")
        answer = ask_kbji(row, titles) if args.sheet_type == "kbji" else ask_dedup(row)
        if answer == "q":
            break
        if answer == "s":
            continue
        row[column] = answer
        save(path, fields, rows)
    done = sum(1 for r in rows if (r.get(column) or "").strip())
    print(f"\n{done} of {len(rows)} labelled, saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
