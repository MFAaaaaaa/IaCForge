#!/usr/bin/env python3
"""Read-only integrity and leakage-boundary checks for the package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import graph_ir
import provider_schema
import result_metrics
import versioning
from iac_kg.offline_provider_contract_cache import (
    cache_entry_matches_online,
    cache_coverage,
    load_offline_provider_contract_entries,
)
from iac_kg.typed_kg import kg_quality_report


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check-online-cache", action="store_true")
    args = parser.parse_args()

    dataset = ROOT / "data" / "complete" / "data.csv"
    with dataset.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    prompts = [row["Prompt"] for row in rows]
    packages = Counter()
    rego_v1 = 0
    for row in rows:
        rego = row["Rego intent"]
        match = re.search(r"^\s*package\s+([^\s]+)", rego, flags=re.MULTILINE)
        packages[match.group(1) if match else "<missing>"] += 1
        rego_v1 += "import rego.v1" in rego

    cache = cache_coverage(prompts)
    cache_entries = load_offline_provider_contract_entries()
    kg_root = (
        ROOT
        / "data"
        / "leakfree_multigranular_kg"
        / "terraform_aws_5.90.0_public_kg"
    )
    manifest = versioning.build_version_manifest(
        provider_schema.SCHEMA_FILE, kg_root
    )
    online_cache_mismatches = []
    if args.check_online_cache:
        from iac_kg.provider_contract_retriever import (
            retrieve_public_provider_contract_evidence,
        )

        for prompt in dict.fromkeys(prompts):
            key = graph_ir.prompt_sha256(prompt)
            entry = cache_entries.get(key)
            if entry is None or not cache_entry_matches_online(
                entry, retrieve_public_provider_contract_evidence(prompt)
            ):
                online_cache_mismatches.append(key)
    result_files = sorted((ROOT / "results").glob("**/*.csv"))
    report = {
        "dataset": {
            "path": str(dataset),
            "sha256": sha256(dataset),
            "rows": len(rows),
            "unique_prompts": len(set(prompts)),
            "columns": list(rows[0]),
        },
        "schema": {
            "path": str(provider_schema.SCHEMA_FILE),
            "sha256": sha256(provider_schema.SCHEMA_FILE),
        },
        "graph_ir": {
            "version": graph_ir.GRAPH_IR_VERSION,
        },
        "version_manifest": manifest,
        "kg_cache": cache,
        "online_cache_equivalence": {
            "checked": bool(args.check_online_cache),
            "mismatches": online_cache_mismatches,
        },
        "kg_cache_provenance": {
            "entries": len(cache_entries),
            "retriever_versions": sorted(
                {item.get("retriever_version", "") for item in cache_entries.values()}
            ),
            "provider_versions": sorted(
                {item.get("provider_version", "") for item in cache_entries.values()}
            ),
            "entries_with_schema_hash": sum(
                bool(item.get("schema_sha256")) for item in cache_entries.values()
            ),
            "entries_with_kg_hash": sum(
                bool(item.get("kg_sha256")) for item in cache_entries.values()
            ),
        },
        "kg_quality": kg_quality_report(kg_root),
        "opa_policies": {
            "packages": dict(packages),
            "rego_v1": rego_v1,
        },
        "tools": {
            "terraform": shutil.which("terraform"),
            "opa": shutil.which("opa"),
        },
        "results": {
            str(path.relative_to(ROOT)): result_metrics.summarize_csv(path)
            for path in result_files
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    failures = []
    if len(rows) != 458:
        failures.append(f"expected 458 dataset rows, found {len(rows)}")
    if cache["missing_prompt_sha256"]:
        failures.append(
            f'offline KG cache misses {len(cache["missing_prompt_sha256"])} prompt hashes'
        )
    if manifest["provider_version"] != versioning.AWS_PROVIDER_VERSION:
        failures.append("provider version manifest is not aligned to 5.90.0")
    if cache_entries and any(
        item.get("retriever_version") != versioning.RETRIEVER_VERSION
        for item in cache_entries.values()
    ):
        failures.append("offline cache was not rebuilt with hybrid-v2")
    if online_cache_mismatches:
        failures.append(
            f"online/offline canonical evidence differs for {len(online_cache_mismatches)} prompts"
        )
    if not (kg_root / "typed_nodes.jsonl").exists() or not (
        kg_root / "typed_edges.jsonl"
    ).exists():
        failures.append("typed KG materialization is missing")
    if not report["tools"]["terraform"]:
        failures.append("terraform executable is missing")
    if not report["tools"]["opa"]:
        failures.append("opa executable is missing")
    if failures:
        print("VERIFY_FAILURES:", *failures, sep="\n- ", file=sys.stderr)
        if args.strict:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
