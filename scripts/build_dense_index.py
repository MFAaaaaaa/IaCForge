#!/usr/bin/env python3
"""Build an optional resource-level semantic index from public KG text only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

from iac_kg.provider_contract_retriever import (
    _load_contract_index,
    _resource_document,
)


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
    parser.add_argument(
        "--model",
        default=os.environ.get("IAC_EMBEDDING_MODEL", "text-embedding-3-small"),
    )
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    index = _load_contract_index(str(root))
    documents = [
        (resource_type, _resource_document(resource_type, contract))
        for resource_type, contract in sorted(index["contracts"].items())
    ]
    client = OpenAI(
        api_key=os.environ.get("IAC_EMBEDDING_API_KEY", "EMPTY"),
        base_url=os.environ.get(
            "IAC_EMBEDDING_BASE_URL",
            os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        ),
    )
    resources = []
    for start in range(0, len(documents), args.batch_size):
        batch = documents[start : start + args.batch_size]
        response = client.embeddings.create(
            model=args.model, input=[text for _, text in batch]
        )
        for (resource_type, text), item in zip(batch, response.data):
            resources.append(
                {
                    "type": resource_type,
                    "text": text,
                    "vector": item.embedding,
                }
            )
    payload = {
        "index_version": "resource-semantic-v1",
        "source_policy": (
            "Public resource/data-source names, descriptions, aliases and short "
            "purpose text only; no benchmark labels or field-level documentation."
        ),
        "embedding_model": args.model,
        "resources": resources,
    }
    output = root / "resource_dense_index.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "resources": len(resources)}, indent=2))


if __name__ == "__main__":
    main()
