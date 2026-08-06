#!/usr/bin/env python3
"""Read-only integrity and method-boundary checks for IaCForge."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import inspect
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import local_repair
import result_metrics


csv.field_size_limit(sys.maxsize)

CORE_MODELS = {"qwen2.5-coder-3b", "qwen2.5-coder-14b"}
ALL_MODELS = {
    "codellama-13b-instruct",
    "mistral-7b-instruct",
    "qwen2.5-coder-3b",
    "qwen2.5-coder-14b",
    "qwen2.5-coder-32b-awq",
    "qwen3-8b",
    "qwen3-14b",
}
EXPECTED_MODE_MODELS = {
    "baseline": ALL_MODELS,
    "baseline_ir": CORE_MODELS,
    "baseline_ir_schema": ALL_MODELS,
    "full_kg": CORE_MODELS,
    "full_kg_repair": CORE_MODELS,
    "paper_kg": CORE_MODELS,
    "paper_kg_repair": CORE_MODELS,
}
EXPECTED_CHROMA_COUNTS = {
    "terraform_resources": 5996,
    "terraform_doc_chunks": 1390,
    "terraform_examples": 422,
    "terraform_arguments_blocks": 4419,
}
EXPECTED_PAPER_SOURCE_COUNTS = {
    "terraform_json_docs_with_summaries": 199,
    "kg_json": 208,
    "reference_relations": 199,
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def jsonl_count(path):
    with Path(path).open(encoding="utf-8") as handle:
        return sum(bool(line.strip()) for line in handle)


def paper_chroma_counts(path):
    uri = f"file:{Path(path).resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
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


def verify_results():
    manifest_path = ROOT / "results" / "RESULT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts", [])
    failures = []
    seen = set()
    records = []
    for artifact in artifacts:
        mode = artifact.get("mode")
        model = artifact.get("model")
        seen.add((mode, model))
        result_path = ROOT / "results" / artifact.get("result", "")
        log_path = ROOT / "results" / artifact.get("log", "")
        for label, path, expected in (
            ("result", result_path, artifact.get("result_sha256")),
            ("log", log_path, artifact.get("log_sha256")),
        ):
            if not path.is_file():
                failures.append(f"missing {label}: {path}")
            elif sha256(path) != expected:
                failures.append(f"hash mismatch for {label}: {path}")
        metrics = result_summary(result_path) if result_path.is_file() else {}
        if metrics != artifact.get("metrics"):
            failures.append(f"metrics mismatch: {result_path}")
        if metrics.get("rows") != 458 or metrics.get("completed") != 458:
            failures.append(f"incomplete result: {result_path}")
        records.append({"mode": mode, "model": model, "metrics": metrics})

    expected = {
        (mode, model)
        for mode, models in EXPECTED_MODE_MODELS.items()
        for model in models
    }
    if len(artifacts) != 24 or seen != expected:
        failures.append(
            "result manifest must contain exactly the 24 retained mode/model pairs"
        )
    return records, failures


def repair_boundary_report():
    signature = list(inspect.signature(local_repair.build_prompt).parameters)
    policy = local_repair.policy_manifest()
    tree = ast.parse((ROOT / "evaluation" / "eval_verigraph.py").read_text(encoding="utf-8"))
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
        "signature": signature,
        "policy": policy,
        "orchestrator_calls": len(calls),
        "orchestrator_positional_args": [len(call.args) for call in calls],
    }


def surface_failures():
    failures = []
    expected_configs = {f"{model}.json" for model in ALL_MODELS}
    actual_configs = {
        path.name for path in (ROOT / "configs" / "models").glob("*.json")
    }
    if actual_configs != expected_configs:
        failures.append(f"model config surface differs: {sorted(actual_configs)}")

    expected_python = {
        "eval_verigraph.py",
        "graph_ir.py",
        "local_repair.py",
        "models.py",
        "opa_evaluator.py",
        "prompt_templates_verigraph.py",
        "provider_contract.py",
        "provider_schema.py",
        "result_metrics.py",
        "schema_rag.py",
        "versioning.py",
    }
    actual_python = {path.name for path in (ROOT / "evaluation").glob("*.py")}
    if actual_python != expected_python:
        failures.append(f"evaluation module surface differs: {sorted(actual_python)}")

    expected_kg_python = {
        "__init__.py",
        "build_paper_replication_chroma.py",
        "paper_replication_json_retriever.py",
        "provider_contract_retriever.py",
    }
    actual_kg_python = {
        path.name for path in (ROOT / "evaluation" / "iac_kg").glob("*.py")
    }
    if actual_kg_python != expected_kg_python:
        failures.append(f"KG module surface differs: {sorted(actual_kg_python)}")

    expected_scripts = {
        "prepare_provider_mirror.sh",
        "run_framework.sh",
        "verify_package.py",
    }
    actual_scripts = {path.name for path in (ROOT / "scripts").iterdir() if path.is_file()}
    if actual_scripts != expected_scripts:
        failures.append(f"script surface differs: {sorted(actual_scripts)}")

    forbidden_terms = [("hy" + "brid").lower(), ("skele" + "ton").lower()]
    scan_paths = [
        *ROOT.glob("*.md"),
        *(ROOT / "evaluation").glob("*.py"),
        *(ROOT / "evaluation" / "iac_kg").glob("*.py"),
        *(ROOT / "evaluation" / "iac_kg").glob("*.md"),
        *(ROOT / "scripts").glob("*.sh"),
        *(ROOT / "data").glob("*.md"),
        *(ROOT / "data" / "full_kg").glob("*.md"),
        *(ROOT / "data" / "paper_kg").glob("*.md"),
    ]
    for path in scan_paths:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for term in forbidden_terms:
            if term in text:
                failures.append(f"retired term remains in {path}: {term}")
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    failures = []
    dataset = ROOT / "data" / "complete" / "data.csv"
    with dataset.open(newline="", encoding="utf-8") as handle:
        dataset_rows = list(csv.DictReader(handle))
    if len(dataset_rows) != 458:
        failures.append(f"expected 458 dataset rows, found {len(dataset_rows)}")

    schema = ROOT / "data" / "schema_grounding" / "aws-provider-schema.json"
    if not schema.is_file():
        failures.append("provider schema is missing")

    full_root = ROOT / "data" / "full_kg" / "provider_kg"
    full_metadata = json.loads((full_root / "metadata.json").read_text(encoding="utf-8"))
    full_counts = {
        "records": jsonl_count(full_root / "resources.jsonl"),
        "edges": jsonl_count(full_root / "kg_edges.jsonl"),
        "docs": len(list((full_root / "docs").glob("*.md"))),
    }
    if full_counts != {"records": 1735, "edges": 3514, "docs": 1724}:
        failures.append(f"Full KG counts differ: {full_counts}")
    if full_metadata.get("target_types") != 1735 or full_metadata.get("dataset_csv") is not None:
        failures.append("Full KG metadata does not describe the complete dataset-independent graph")

    paper_base = ROOT / "data" / "paper_kg"
    paper_source = paper_base / "source" / "notebooks_kg_construction"
    paper_source_counts = {
        name: len(list((paper_source / name).glob("*.json")))
        for name in EXPECTED_PAPER_SOURCE_COUNTS
    }
    if paper_source_counts != EXPECTED_PAPER_SOURCE_COUNTS:
        failures.append(f"Paper KG source counts differ: {paper_source_counts}")
    chroma_counts = paper_chroma_counts(paper_base / "chroma" / "chroma.sqlite3")
    if chroma_counts != EXPECTED_CHROMA_COUNTS:
        failures.append(f"Paper KG Chroma counts differ: {chroma_counts}")

    results, result_failures = verify_results()
    failures.extend(result_failures)

    repair = repair_boundary_report()
    if repair["signature"] != [
        "question_prompt",
        "graph_ir",
        "schema_context",
        "current_hcl",
        "diagnostic",
    ]:
        failures.append("local repair accepts an unexpected input parameter")
    if repair["orchestrator_calls"] != 1 or repair["orchestrator_positional_args"] != [5]:
        failures.append("local repair call shape differs")
    policy = repair["policy"]
    if not (
        policy.get("max_calls") == 1
        and policy.get("switch") == "VERIGRAPH_MAX_REPAIR_STEPS"
        and policy.get("raw_kg_in_repair") is False
        and policy.get("provider_contract_in_repair") is False
        and policy.get("opa_feedback_used") is False
    ):
        failures.append("local repair boundary differs")

    failures.extend(surface_failures())
    report = {
        "dataset": {"rows": len(dataset_rows), "sha256": sha256(dataset)},
        "schema": {"sha256": sha256(schema)},
        "full_kg": {"counts": full_counts, "metadata": full_metadata},
        "paper_kg": {
            "source_counts": paper_source_counts,
            "chroma_counts": chroma_counts,
        },
        "repair_boundary": repair,
        "results": results,
        "failures": failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if failures:
        print("VERIFY_FAILURES:", *failures, sep="\n- ", file=sys.stderr)
        if args.strict:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
