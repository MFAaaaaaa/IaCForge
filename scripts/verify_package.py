#!/usr/bin/env python3
"""Read-only integrity and leakage-boundary checks for the package."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import inspect
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import graph_ir
import local_repair
import provider_schema
import result_metrics
import versioning
from iac_kg.offline_provider_contract_cache import (
    cache_entry_matches_online,
    cache_coverage,
    load_offline_provider_contract_entries,
)
from iac_kg.typed_kg import kg_quality_report


csv.field_size_limit(sys.maxsize)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


EXPECTED_CHROMA_COUNTS = {
    "terraform_resources": 5996,
    "terraform_doc_chunks": 1390,
    "terraform_examples": 422,
    "terraform_arguments_blocks": 4419,
}


def load_jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def paper_chroma_counts(path):
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT collections.name, COUNT(*)
            FROM embeddings
            JOIN segments ON embeddings.segment_id = segments.id
            JOIN collections ON segments.collection = collections.id
            GROUP BY collections.name
            """
        ).fetchall()
    return dict(rows)


def result_summary(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    summary = result_metrics.summarize_rows(rows)
    summary["completed"] = sum(
        bool(str(row.get("LLM Output #0", "")).strip()) for row in rows
    )
    summary["pass_at_1"] = summary.pop("opa")
    return summary


def verify_result_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    records = []
    failures = []
    for artifact in manifest.get("artifacts", []):
        result_path = ROOT / "results" / artifact["result"]
        log_path = ROOT / "results" / artifact["log"]
        record = {
            "group": artifact.get("group"),
            "variant": artifact.get("variant"),
            "model": artifact.get("model"),
            "result": artifact.get("result"),
            "log": artifact.get("log"),
        }
        for label, artifact_path, expected_hash in (
            ("result", result_path, artifact.get("result_sha256")),
            ("log", log_path, artifact.get("log_sha256")),
        ):
            if not artifact_path.is_file():
                failures.append(f"manifest {label} is missing: {artifact_path}")
                continue
            actual_hash = sha256(artifact_path)
            if actual_hash != expected_hash:
                failures.append(
                    f"manifest {label} hash mismatch: {artifact_path}"
                )
        if result_path.is_file():
            actual_metrics = result_summary(result_path)
            record["metrics"] = actual_metrics
            if actual_metrics != artifact.get("metrics"):
                failures.append(f"manifest metrics mismatch: {result_path}")
            if actual_metrics["rows"] != 458 or actual_metrics["completed"] != 458:
                failures.append(f"result is not complete full458: {result_path}")
        records.append(record)

    missing = manifest.get("missing", [])
    expected_missing = {
        (
            "kg_repair",
            "paperkg/both_localrepair1_no_direct_kg",
            "qwen2.5-coder-14b",
            False,
        )
    }
    actual_missing = {
        (
            item.get("group"),
            item.get("variant"),
            item.get("model"),
            item.get("required"),
        )
        for item in missing
    }
    if actual_missing != expected_missing:
        failures.append("result manifest has an unexpected missing-artifact set")
    if len(records) != 41:
        failures.append(f"expected 41 result/log pairs, found {len(records)}")
    return {
        "artifacts": records,
        "artifact_count": len(records),
        "missing": missing,
    }, failures


def repair_boundary_report():
    signature = inspect.signature(local_repair.build_prompt)
    policy = local_repair.policy_manifest()
    source_path = ROOT / "evaluation" / "eval_verigraph.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "local_repair"
        and node.func.attr == "build_prompt"
    ]
    return {
        "signature": list(signature.parameters),
        "policy": policy,
        "orchestrator_calls": len(calls),
        "orchestrator_positional_args": [len(call.args) for call in calls],
    }


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
    result_manifest, result_failures = verify_result_manifest(
        ROOT / "results" / "RESULT_MANIFEST.json"
    )
    paper_source = (
        ROOT / "data" / "paper_kg" / "source" / "notebooks_kg_construction"
    )
    paper_source_counts = {
        name: len(list((paper_source / name).glob("*.json")))
        for name in (
            "terraform_json_docs_with_summaries",
            "kg_json",
            "reference_relations",
        )
    }
    chroma_path = ROOT / "data" / "paper_kg" / "chroma" / "chroma.sqlite3"
    chroma_counts = paper_chroma_counts(chroma_path)
    hybrid_evidence_path = (
        ROOT
        / "data"
        / "hybrid_paper_fullkg"
        / "evidence_v1"
        / "publickg_full458.jsonl"
    )
    hybrid_evidence = load_jsonl(hybrid_evidence_path)
    hybrid_hashes = {item.get("prompt_sha256") for item in hybrid_evidence}
    prompt_hashes = {graph_ir.prompt_sha256(prompt) for prompt in prompts}
    hybrid_kg_root = (
        ROOT / "data" / "hybrid_paper_fullkg" / "kg_v2_rebuilt"
    )
    hybrid_metadata = json.loads(
        (hybrid_kg_root / "metadata.json").read_text(encoding="utf-8")
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
        "paper_kg": {
            "source_json_counts": paper_source_counts,
            "chroma_counts": chroma_counts,
            "chroma_total": sum(chroma_counts.values()),
            "embedding_model": "sentence-transformers/all-mpnet-base-v2",
        },
        "hybrid_kg": {
            "evidence_records": len(hybrid_evidence),
            "evidence_unique_prompt_hashes": len(hybrid_hashes),
            "covers_dataset_prompt_hashes": hybrid_hashes == prompt_hashes,
            "rebuilt_kg_records": sum(
                1 for _ in (hybrid_kg_root / "resources.jsonl").open()
            ),
            "rebuilt_kg_edges": sum(
                1 for _ in (hybrid_kg_root / "kg_edges.jsonl").open()
            ),
            "metadata": hybrid_metadata,
        },
        "repair_boundary": repair_boundary_report(),
        "opa_policies": {
            "packages": dict(packages),
            "rego_v1": rego_v1,
        },
        "tools": {
            "terraform": shutil.which("terraform"),
            "opa": shutil.which("opa"),
        },
        "results": result_manifest,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    failures = list(result_failures)
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
    if paper_source_counts != {
        "terraform_json_docs_with_summaries": 199,
        "kg_json": 208,
        "reference_relations": 199,
    }:
        failures.append(f"paper KG source counts differ: {paper_source_counts}")
    if chroma_counts != EXPECTED_CHROMA_COUNTS:
        failures.append(f"paper Chroma counts differ: {chroma_counts}")
    if hybrid_hashes != prompt_hashes:
        failures.append("hybrid evidence-v1 does not cover every dataset Prompt hash")
    if hybrid_metadata.get("provider_version") != "5.90.0":
        failures.append("hybrid rebuilt KG is not aligned to AWS provider 5.90.0")
    if hybrid_metadata.get("records") != 1735 or hybrid_metadata.get("edges") != 3514:
        failures.append("hybrid rebuilt KG record/edge metadata differs")
    repair_report = report["repair_boundary"]
    if repair_report["signature"] != [
        "question_prompt",
        "graph_ir",
        "schema_context",
        "current_hcl",
        "diagnostic",
    ]:
        failures.append("local repair accepts an unexpected input parameter")
    if repair_report["orchestrator_calls"] != 1 or repair_report[
        "orchestrator_positional_args"
    ] != [5]:
        failures.append("local repair orchestrator call shape differs")
    repair_policy = repair_report["policy"]
    if (
        repair_policy.get("trigger") != "terraform_plan_failed"
        or repair_policy.get("max_calls") != 1
        or repair_policy.get("raw_kg_in_repair") is not False
        or repair_policy.get("provider_contract_in_repair") is not False
        or repair_policy.get("opa_feedback_used") is not False
    ):
        failures.append("local repair leakage policy differs")
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
