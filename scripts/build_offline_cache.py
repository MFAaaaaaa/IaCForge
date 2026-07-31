#!/usr/bin/env python3
"""Rebuild the reproducible prompt cache with complete retrieval provenance."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import provider_schema
import versioning
from iac_kg.offline_provider_contract_cache import make_cache_entry
from iac_kg.provider_contract_retriever import (
    retrieval_rules_sha256,
    retrieve_public_provider_contract_evidence,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", default=str(ROOT / "data" / "complete" / "data.csv")
    )
    parser.add_argument(
        "--kg-root",
        default=str(
            ROOT
            / "data"
            / "leakfree_multigranular_kg"
            / "terraform_aws_5.90.0_public_kg"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(
            ROOT
            / "data"
            / "leakfree_multigranular_kg"
            / "offline_retrieval"
            / "provider_contract_full458.jsonl"
        ),
    )
    parser.add_argument("--prompt-column", default="Prompt")
    args = parser.parse_args()

    dataset = Path(args.dataset).resolve()
    kg_root = Path(args.kg_root).resolve()
    output = Path(args.output).resolve()
    os.environ["IAC_PROVIDER_CONTRACT_ROOT"] = str(kg_root)
    os.environ["IAC_KG_REPLICATION_ROOT"] = str(kg_root)
    versioning.assert_version_alignment(
        json.loads((kg_root / "metadata.json").read_text(encoding="utf-8")).get(
            "provider_version", ""
        )
    )
    with dataset.open(encoding="utf-8", newline="") as handle:
        prompts = [row[args.prompt_column] for row in csv.DictReader(handle)]

    schema_hash = versioning.file_sha256(provider_schema.SCHEMA_FILE)
    kg_hash = versioning.file_sha256(kg_root / "metadata.json")
    entries = {}
    for prompt in prompts:
        evidence = json.loads(retrieve_public_provider_contract_evidence(prompt))
        parameters = evidence.get("retrieval_method", {}).get(
            "retrieval_parameters", {}
        )
        entry = make_cache_entry(
            prompt,
            evidence,
            kg_sha256=kg_hash,
            schema_sha256=schema_hash,
            retrieval_parameters=parameters,
        )
        entries[entry["prompt_sha256"]] = entry

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for key in sorted(entries):
            handle.write(
                json.dumps(entries[key], ensure_ascii=False, sort_keys=True) + "\n"
            )
    temporary.replace(output)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_csv": str(dataset),
        "output_jsonl": str(output),
        "rows_read": len(prompts),
        "unique_prompts": len(set(prompts)),
        "provider_version": versioning.AWS_PROVIDER_VERSION,
        "retriever_version": versioning.RETRIEVER_VERSION,
        "retrieval_rules_sha256": retrieval_rules_sha256(),
        "schema_sha256": schema_hash,
        "kg_sha256": kg_hash,
        "cache_sha256": versioning.file_sha256(output),
    }
    output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
