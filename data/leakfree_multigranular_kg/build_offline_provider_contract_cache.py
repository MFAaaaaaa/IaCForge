"""Build prompt-hash keyed offline KG retrieval evidence for IaC-Eval prompts.

The cache is a runtime optimization for model experiments. It is built only
from the visible `Prompt` column and the leakage-free public provider KG.
It does not read IaC-Eval Resource, Intent, Rego intent, Reference output,
validation/plan/OPA feedback, generated HCL, or feedback traces.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT_DIR / "data" / "complete" / "data.csv"
DEFAULT_KG_ROOT = (
    ROOT_DIR
    / "data"
    / "leakfree_multigranular_kg"
    / "terraform_aws_5.90.0_public_kg"
)
DEFAULT_SCHEMA = ROOT_DIR / "data" / "schema_grounding" / "aws-provider-schema.json"
DEFAULT_OUTPUT = (
    ROOT_DIR
    / "data"
    / "leakfree_multigranular_kg"
    / "offline_retrieval"
    / "provider_contract_full458.jsonl"
)


def prompt_sha256(prompt):
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def read_prompts(dataset_csv, max_rows=None):
    prompts = []
    with Path(dataset_csv).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "Prompt" not in (reader.fieldnames or []):
            raise ValueError(f"{dataset_csv} does not contain a Prompt column")
        for row in reader:
            prompts.append(row["Prompt"])
            if max_rows is not None and len(prompts) >= max_rows:
                break
    return prompts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-csv", default=str(DEFAULT_DATASET))
    parser.add_argument("--kg-root", default=str(DEFAULT_KG_ROOT))
    parser.add_argument("--schema-json", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-rows", type=int, default=None)
    args = parser.parse_args()

    evaluation_dir = ROOT_DIR / "evaluation"
    sys.path.insert(0, str(evaluation_dir))
    os.environ.setdefault("IAC_PROVIDER_CONTRACT_ROOT", str(Path(args.kg_root).resolve()))
    os.environ.setdefault("IAC_KG_REPLICATION_ROOT", str(Path(args.kg_root).resolve()))
    os.environ.setdefault("IAC_SCHEMA_FILE", str(Path(args.schema_json).resolve()))

    from iac_kg.provider_contract_retriever import (
        public_provider_contract_available,
        retrieve_public_provider_contract_evidence,
    )

    if not public_provider_contract_available():
        raise FileNotFoundError(f"Provider contract KG is not available under {args.kg_root}")

    prompts = read_prompts(args.dataset_csv, args.max_rows)
    output = Path(args.output_jsonl).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    seen = {}
    with output.open("w", encoding="utf-8") as f:
        for index, prompt in enumerate(prompts):
            key = prompt_sha256(prompt)
            if key in seen:
                continue
            evidence = retrieve_public_provider_contract_evidence(prompt)
            evidence_payload = json.loads(evidence)
            item = {
                "prompt_sha256": key,
                "evidence_sha256": prompt_sha256(evidence),
                "evidence_kind": evidence_payload.get("evidence_kind", ""),
                "contract_kind": evidence_payload.get("contract_kind", ""),
                "evidence": evidence_payload,
            }
            f.write(json.dumps(item, sort_keys=True) + "\n")
            seen[key] = index

    kg_metadata_path = Path(args.kg_root) / "metadata.json"
    kg_metadata = json.loads(kg_metadata_path.read_text()) if kg_metadata_path.exists() else {}
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(Path(args.dataset_csv).resolve()),
        "prompt_column": "Prompt",
        "output_jsonl": str(output),
        "rows_read": len(prompts),
        "unique_prompts": len(seen),
        "kg_root": str(Path(args.kg_root).resolve()),
        "kg_metadata": {
            "provider_version": kg_metadata.get("provider_version"),
            "target_types": kg_metadata.get("target_types"),
            "records": kg_metadata.get("records"),
            "edges": kg_metadata.get("edges"),
            "dataset_csv": kg_metadata.get("dataset_csv"),
            "paper_kg_source": kg_metadata.get("paper_kg_source"),
        },
        "leakage_policy": (
            "Built only from IaC-Eval Prompt text plus public Terraform AWS provider docs/schema KG. "
            "No IaC-Eval Resource, Intent, Rego intent, Reference output, validation result, "
            "plan result, OPA result, generated HCL, or feedback trace is used."
        ),
    }
    metadata_path = output.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Wrote {metadata_path}")
    print(f"rows_read={len(prompts)} unique_prompts={len(seen)}")


if __name__ == "__main__":
    main()
