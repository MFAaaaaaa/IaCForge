"""Version-chain and canonical hashing helpers for reproducible experiments."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


TERRAFORM_VERSION = "1.9.8"
AWS_PROVIDER_VERSION = "5.90.0"
RETRIEVER_VERSION = "hybrid-v2"
CONTRACT_VERSION = "2.0"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provider_constraint() -> str:
    return os.environ.get(
        "AWS_PROVIDER_VERSION_CONSTRAINT", f"= {AWS_PROVIDER_VERSION}"
    ).strip()


def assert_version_alignment(provider_version: str = "") -> None:
    expected = AWS_PROVIDER_VERSION
    configured = provider_constraint().lstrip("= ").strip()
    actual = str(provider_version or expected).strip()
    if configured != expected or actual != expected:
        raise ValueError(
            "Provider version drift detected: "
            f"runtime={configured!r}, knowledge={actual!r}, expected={expected!r}. "
            "Rebuild schema, docs, KG and cache together before changing versions."
        )


def build_version_manifest(
    schema_path: str | Path,
    kg_root: str | Path | None = None,
) -> dict[str, Any]:
    kg_root = Path(kg_root) if kg_root else None
    metadata: dict[str, Any] = {}
    if kg_root and (kg_root / "metadata.json").exists():
        metadata = json.loads((kg_root / "metadata.json").read_text(encoding="utf-8"))
    provider_version = str(metadata.get("provider_version") or AWS_PROVIDER_VERSION)
    assert_version_alignment(provider_version)
    return {
        "terraform_version": TERRAFORM_VERSION,
        "provider_name": "hashicorp/aws",
        "provider_version": provider_version,
        "schema_sha256": file_sha256(schema_path),
        "kg_sha256": file_sha256(kg_root / "metadata.json") if kg_root else "",
        "retriever_version": RETRIEVER_VERSION,
        "contract_version": CONTRACT_VERSION,
    }
