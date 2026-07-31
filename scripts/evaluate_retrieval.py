#!/usr/bin/env python3
"""Evaluate resource retrieval after generation-free candidate construction."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

from iac_kg.offline_provider_contract_cache import (
    get_offline_provider_contract_entry,
)
from retrieval_metrics import resource_retrieval_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", default=str(ROOT / "data" / "complete" / "data.csv")
    )
    parser.add_argument("--gold-column", default="Resource")
    args = parser.parse_args()
    with Path(args.dataset).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    metrics = []
    for row in rows:
        entry = get_offline_provider_contract_entry(row["Prompt"])
        candidates = [
            item.get("type", "")
            for item in (entry or {}).get("evidence", {}).get(
                "candidate_resources", []
            )
        ]
        metrics.append(
            resource_retrieval_metrics(candidates, row.get(args.gold_column, ""))
        )
    keys = sorted({key for row in metrics for key in row})
    summary = {
        key: sum(float(row.get(key, 0)) for row in metrics) / len(metrics)
        for key in keys
    }
    summary["rows"] = len(metrics)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
