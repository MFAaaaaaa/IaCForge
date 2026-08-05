#!/usr/bin/env python3
"""Assemble the paper-facing result archive from trusted iac-eval outputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


csv.field_size_limit(sys.maxsize)


@dataclass(frozen=True)
class Artifact:
    group: str
    variant: str
    model: str
    result: str
    log: str
    required: bool = True


def result(model, suffix):
    return f"results/{model}/complete/evaluation-dataset-for-data-{suffix}.csv"


ARTIFACTS = [
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "baseline-compilemetric-full458-v1"), "logs/qwen25coder3b-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "baseline-compilemetric-full458-v1"), "logs/qwen25coder14b-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "qwen2.5-coder-32b-awq", result("qwen2.5-coder-32b", "baseline-compilemetric-full458-awq-norepair-v1"), "logs/qwen25coder32b-awq-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "mistral-7b-instruct", result("mistral-7b-instruct", "baseline-compilemetric-full458-v1"), "logs/mistral7b-instruct-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "qwen3-14b", result("qwen3-14b", "baseline-compilemetric-full458-v1"), "logs/qwen3-14b-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "codellama-13b-instruct", result("codellama-13b-instruct", "baseline-compilemetric-full458-v1"), "logs/codellama13b-instruct-baseline-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "baseline", "qwen3-8b", result("qwen3-8b", "baseline-compilemetric-full458-v1"), "logs/qwen3-8b-baseline-compilemetric-full458-v1.log"),

    Artifact("clean_multigranular_kg_and_ablations", "ir_only", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "GraphIR-ironly-noschema-nokg-norepair-promptonly-full458-v1-32k-maxtok1536-20260803"), "logs/qwen25coder3b-ironly-noschema-nokg-norepair-promptonly-full458-v1-32k-maxtok1536-20260803.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_only", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "GraphIR-ironly-noschema-nokg-norepair-promptonly-full458-v1-32k-maxtok1536-20260803"), "logs/qwen25coder14b-ironly-noschema-nokg-norepair-promptonly-full458-v1-32k-maxtok1536-20260803.log"),

    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "VeriGraph-graphir-promptschema-norepair-compilemetric-full458-v1"), "logs/qwen25coder3b-graphir-promptschema-norepair-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "VeriGraph-graphir-promptschema-norepair-compilemetric-full458-v1"), "logs/qwen25coder14b-graphir-promptschema-norepair-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "qwen2.5-coder-32b-awq", result("qwen2.5-coder-32b", "VeriGraph-graphir-promptschema-norepair-compilemetric-full458-awq-v1"), "logs/qwen25coder32b-awq-graphir-promptschema-norepair-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "mistral-7b-instruct", result("mistral-7b-instruct", "VeriGraph-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1"), "logs/mistral7b-instruct-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "qwen3-14b", result("qwen3-14b", "VeriGraph-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1"), "logs/qwen3-14b-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "codellama-13b-instruct", result("codellama-13b-instruct", "VeriGraph-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1"), "logs/codellama13b-instruct-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema", "qwen3-8b", result("qwen3-8b", "VeriGraph-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1"), "logs/qwen3-8b-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1.log"),

    Artifact("clean_multigranular_kg_and_ablations", "ir_schema_multigranular_kg", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v2-32k-maxtok1536-20260727"), "logs/qwen25coder3b-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v2-32k-maxtok1536-20260727.log"),
    Artifact("clean_multigranular_kg_and_ablations", "ir_schema_multigranular_kg", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v1-32k-maxtok1536-20260727"), "logs/qwen25coder14b-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v1-32k-maxtok1536-20260727.log"),

    Artifact("kg_repair", "injection_stage_paperkg/ir", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-ironly-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder3b-paperrepjson-ironly-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),
    Artifact("kg_repair", "injection_stage_paperkg/ir", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-ironly-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder14b-paperrepjson-ironly-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),
    Artifact("kg_repair", "injection_stage_paperkg/hcl", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-norepair-promptonly-full458-v1-20260720"), "logs/qwen25coder3b-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-norepair-promptonly-full458-v1-20260720.log"),
    Artifact("kg_repair", "injection_stage_paperkg/hcl", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-norepair-promptonly-full458-v1-20260720"), "logs/qwen25coder14b-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-norepair-promptonly-full458-v1-20260720.log"),
    Artifact("kg_repair", "injection_stage_paperkg/both", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder3b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),
    Artifact("kg_repair", "injection_stage_paperkg/both", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder14b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),

    Artifact("kg_repair", "paperkg/ir_localrepair1_no_direct_kg", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-ironly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721"), "logs/qwen25coder3b-paperrepjson-ironly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721.log"),
    Artifact("kg_repair", "paperkg/ir_localrepair1_no_direct_kg", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-ironly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721"), "logs/qwen25coder14b-paperrepjson-ironly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721.log"),
    Artifact("kg_repair", "paperkg/hcl_localrepair1_no_direct_kg", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721"), "logs/qwen25coder3b-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-v1-20260721.log"),
    Artifact("kg_repair", "paperkg/hcl_localrepair1_no_direct_kg", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-ctx32k-out4096-v1-20260721"), "logs/qwen25coder14b-paperrepjson-hclonly-faithfulgraph-graphir-promptschema-localrepair1-promptonly-full458-ctx32k-out4096-v1-20260721.log"),

    Artifact("kg_repair", "paperkg/both", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder3b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),
    Artifact("kg_repair", "paperkg/both", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722"), "logs/qwen25coder14b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260722.log"),
    Artifact("kg_repair", "paperkg/both_localrepair1_no_direct_kg", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok16384-20260803"), "logs/qwen25coder3b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok16384-20260803.log"),
    Artifact("kg_repair", "paperkg/both_localrepair1_no_direct_kg", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok16384-20260804"), "logs/qwen25coder14b-paperrepjson-both-faithfulgraph-simplegraphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok16384-20260804.log"),

    Artifact("kg_repair", "half_paper_half_fullkg/hybridnested", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-paperrepjson-hybridnested-graphir-promptschema-norepair-promptonly-full458-v3-20260717"), "logs/qwen25coder3b-paperrepjson-hybridnested-graphir-promptschema-norepair-promptonly-full458-v3-20260717.log"),
    Artifact("kg_repair", "half_paper_half_fullkg/hybridnested", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-paperrepjson-hybridnested-graphir-promptschema-norepair-promptonly-full458-v3-20260717"), "logs/qwen25coder14b-paperrepjson-hybridnested-graphir-promptschema-norepair-promptonly-full458-v3-20260717.log"),
    Artifact("kg_repair", "half_paper_half_fullkg/both", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-publickg-offline-both-graphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260723"), "logs/qwen25coder3b-publickg-offline-both-graphir-promptschema-norepair-promptonly-full458-maxtok8192-v1-20260723.log"),
    Artifact("kg_repair", "half_paper_half_fullkg/both", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-publickg-offline-both-graphir-promptschema-norepair-promptonly-full458-ctx32k-out16k-maxio-v1-20260725"), "logs/qwen25coder14b-publickg-offline-both-graphir-promptschema-norepair-promptonly-full458-ctx32k-out16k-maxio-v1-20260725.log"),
    Artifact("kg_repair", "half_paper_half_fullkg/both_localrepair1", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-publickg-offline-both-graphir-promptschema-localrepair1-promptonly-full458-maxtok8192-v1-20260723"), "logs/qwen25coder3b-publickg-offline-both-graphir-promptschema-localrepair1-promptonly-full458-maxtok8192-v1-20260723.log"),
    Artifact("kg_repair", "half_paper_half_fullkg/both_localrepair1", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-publickg-offline-both-graphir-promptschema-localrepair1-promptonly-full458-maxtok8192-v1-20260723"), "logs/qwen25coder14b-publickg-offline-both-graphir-promptschema-localrepair1-promptonly-full458-maxtok8192-v1-20260723.log"),

    Artifact("kg_repair", "clean_multigranular_kg/both", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v2-32k-maxtok1536-20260727"), "logs/qwen25coder3b-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v2-32k-maxtok1536-20260727.log"),
    Artifact("kg_repair", "clean_multigranular_kg/both", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v1-32k-maxtok1536-20260727"), "logs/qwen25coder14b-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v1-32k-maxtok1536-20260727.log"),
    Artifact("kg_repair", "clean_multigranular_kg/both_localrepair1", "qwen2.5-coder-3b", result("qwen2.5-coder-3b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok1536-20260728"), "logs/qwen25coder3b-multigranularkg-both-graphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok1536-20260728.log"),
    Artifact("kg_repair", "clean_multigranular_kg/both_localrepair1", "qwen2.5-coder-14b", result("qwen2.5-coder-14b", "FullKGVeriGraph-multigranularkg-both-graphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok1536-20260728"), "logs/qwen25coder14b-multigranularkg-both-graphir-promptschema-localrepair1-promptonly-full458-v1-32k-maxtok1536-20260728.log"),
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metrics(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    completed = sum(bool(str(row.get("LLM Output #0", "")).strip()) for row in rows)
    if completed != len(rows):
        raise ValueError(f"Incomplete result: {path} has {completed}/{len(rows)} completed rows")
    return {
        "rows": len(rows),
        "completed": completed,
        "validate": sum(str(row.get("LLM Compilable? #0", "")).lower() == "true" for row in rows),
        "plan": sum(str(row.get("LLM Plannable? #0", "")).lower() == "true" for row in rows),
        "pass_at_1": sum(str(row.get("LLM Correct? #0", "")).lower() in {"true", "success"} for row in rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    manifest = {"source": str(source), "artifacts": [], "missing": []}
    for item in ARTIFACTS:
        result_source = source / item.result
        log_source = source / item.log
        missing = [str(path) for path in (result_source, log_source) if not path.exists()]
        if missing:
            record = {"group": item.group, "variant": item.variant, "model": item.model, "missing": missing, "required": item.required}
            manifest["missing"].append(record)
            if item.required:
                raise FileNotFoundError(json.dumps(record, indent=2))
            continue
        target = destination / item.group / item.variant / item.model
        target.mkdir(parents=True, exist_ok=True)
        result_target = target / result_source.name
        log_target = target / log_source.name
        shutil.copy2(result_source, result_target)
        shutil.copy2(log_source, log_target)
        manifest["artifacts"].append({
            "group": item.group,
            "variant": item.variant,
            "model": item.model,
            "result": str(result_target.relative_to(destination)),
            "log": str(log_target.relative_to(destination)),
            "result_sha256": sha256(result_target),
            "log_sha256": sha256(log_target),
            "metrics": metrics(result_target),
        })
    (destination / "RESULT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"copied": len(manifest["artifacts"]), "missing": manifest["missing"]}, indent=2))


if __name__ == "__main__":
    main()
