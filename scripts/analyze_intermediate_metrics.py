#!/usr/bin/env python3
"""Separate Prompt->candidate errors from candidate->Graph-IR errors."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

from retrieval_metrics import _normalize_gold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result_csv")
    parser.add_argument("--gold-column", default="Resource")
    args = parser.parse_args()
    with Path(args.result_csv).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    counts = {
        "rows": len(rows),
        "retrieval_has_gold_ir_misses_gold": 0,
        "retrieval_misses_gold_ir_recovers_gold": 0,
        "retrieval_and_ir_have_gold": 0,
        "retrieval_and_ir_miss_gold": 0,
    }
    for row in rows:
        gold = _normalize_gold(row.get(args.gold_column, ""))
        try:
            notes = json.loads(row.get("LLM Notes #0", "") or "{}")
        except json.JSONDecodeError:
            notes = {}
        retrieved = set(notes.get("retrieval", {}).get("candidate_types", []))
        ir_types = set(notes.get("graph_ir", {}).get("resource_types", []))
        retrieval_ok = bool(gold & retrieved)
        ir_ok = bool(gold & ir_types)
        if retrieval_ok and not ir_ok:
            counts["retrieval_has_gold_ir_misses_gold"] += 1
        elif not retrieval_ok and ir_ok:
            counts["retrieval_misses_gold_ir_recovers_gold"] += 1
        elif retrieval_ok and ir_ok:
            counts["retrieval_and_ir_have_gold"] += 1
        else:
            counts["retrieval_and_ir_miss_gold"] += 1
    print(json.dumps(counts, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
