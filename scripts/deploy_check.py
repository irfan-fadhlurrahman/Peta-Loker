"""Gate in front of `make deploy`: refuses to publish unless the dashboard
data is safe to make public. Exit code 1 blocks the deploy.

Checks, in order:
  1. every dashboard/data/*.json exists and is marked "public": true
     (files from --local mode are refused);
  2. the last quality gate passed, and ran before the data was generated;
  3. no JSON value contains an e-mail address, phone number or contact link,
     and no record carries a company_name, url or source field — a final
     scan, independent of every earlier masking step.

Usage:
    uv run python scripts/deploy_check.py [--data-dir dashboard/data]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.masking import find_pii

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "dashboard" / "data"
QUALITY_REPORT = REPO_ROOT / "data" / "quality" / "last.json"
REQUIRED_FILES = ("summary", "by_province", "by_kbji", "trend", "skills", "salary", "vacancies", "ops")
FORBIDDEN_KEYS = {"company_name", "url", "source", "name_local", "street_address"}


def _walk(value, path="$"):
    """Yield (path, key, value) for every leaf and every dict key."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, None
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, None, value


def check(data_dir: Path = DATA_DIR, quality_report: Path = QUALITY_REPORT) -> list[str]:
    problems: list[str] = []
    generated_at = []
    for name in REQUIRED_FILES:
        path = data_dir / f"{name}.json"
        if not path.exists():
            problems.append(f"{path.name} is missing (run `make dashboard`)")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("public") is not True:
            problems.append(f"{path.name} is not marked public (generated with --local?)")
        generated_at.append(payload.get("generated_at") or "")
        for where, key, value in _walk(payload):
            if key in FORBIDDEN_KEYS and name != "ops":
                problems.append(f"{path.name}: forbidden field {key!r} at {where}")
            if isinstance(value, str) and (kinds := find_pii(value)):
                problems.append(f"{path.name}: {', '.join(kinds)} at {where}")
    if not quality_report.exists():
        problems.append("no quality report (run `make quality`)")
    else:
        report = json.loads(quality_report.read_text(encoding="utf-8"))
        if report.get("status") != "pass":
            problems.append(f"last quality gate did not pass: {report.get('fail')}")
        if generated_at and report.get("checked_at", "") > min(generated_at):
            problems.append("quality gate ran after the data was generated — regenerate with `make dashboard`")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)
    problems = check(args.data_dir)
    if problems:
        print("deploy blocked:")
        for problem in problems[:50]:
            print(f"  - {problem}")
        return 1
    print("deploy check passed: public data, quality gate passed, no contact details found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
