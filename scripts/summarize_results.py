#!/usr/bin/env python3
"""Summarize archived and newly generated result CSVs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

from result_metrics import summarize_csv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="CSV files. Defaults to every CSV under results/.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = args.paths or sorted((ROOT / "results").glob("**/*.csv"))
    rows = []
    for path in paths:
        summary = summarize_csv(path)
        summary["path"] = str(path)
        rows.append(summary)
    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    for row in rows:
        print(
            f'{row["path"]}: rows={row["rows"]} '
            f'validate={row["validate"]} plan={row["plan"]} opa={row["opa"]}'
        )


if __name__ == "__main__":
    main()
