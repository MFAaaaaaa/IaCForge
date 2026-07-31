#!/usr/bin/env python3
"""Materialize stable typed KG nodes/edges from the public provider package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

from iac_kg.typed_kg import (
    _load_jsonl,
    normalize_reference_edge,
    record_to_edges,
    record_to_entities,
)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(
            ROOT
            / "data"
            / "leakfree_multigranular_kg"
            / "terraform_aws_5.90.0_public_kg"
        ),
    )
    args = parser.parse_args()
    root = Path(args.root)
    records = _load_jsonl(root / "resources.jsonl")
    entities = {}
    edges = {}
    for record in records:
        for item in record_to_entities(record):
            entities[item["id"]] = item
        for item in record_to_edges(record):
            edges[item["id"]] = item
    for raw in _load_jsonl(root / "kg_edges.jsonl"):
        item = normalize_reference_edge(raw)
        edges[item["edge_id"]] = item
    _write_jsonl(root / "typed_nodes.jsonl", entities.values())
    _write_jsonl(root / "typed_edges.jsonl", edges.values())
    print(json.dumps({"nodes": len(entities), "edges": len(edges)}, indent=2))


if __name__ == "__main__":
    main()
