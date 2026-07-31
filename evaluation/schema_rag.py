"""IR-guided exact schema grounding.

This is deliberately symbolic rather than vector retrieval: normalized Graph
IR instance types are exact keys into the version-aligned provider schema.
Only required and task-relevant fields are projected into the compiler stage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import provider_schema


SCHEMA_RAG_VERSION = "2.0"


@dataclass(frozen=True)
class SchemaRetrieval:
    requested_types: tuple[str, ...]
    retrieved_types: tuple[str, ...]
    missing_types: tuple[str, ...]
    context: str
    schema_sha256: str
    projection: dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "schema_rag_version": SCHEMA_RAG_VERSION,
            "retrieval_method": "ir_guided_exact_schema_grounding",
            "requested_types": list(self.requested_types),
            "retrieved_types": list(self.retrieved_types),
            "missing_types": list(self.missing_types),
            "hallucinated_resource_type_rate": (
                len(self.missing_types) / len(self.requested_types)
                if self.requested_types
                else 0.0
            ),
            "negative_constraints": self.projection.get("negative_constraints", []),
            "schema_sha256": self.schema_sha256,
            "schema_projection": self.projection,
        }


def _unique_types(resource_types: Iterable[str]) -> list[str]:
    values: list[str] = []
    for value in resource_types:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def schema_sha256() -> str:
    path = Path(provider_schema.SCHEMA_FILE)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def retrieve_schema(resource_types: Iterable[str]) -> SchemaRetrieval:
    """Backward-compatible type-only lookup used by archived callers."""

    requested = _unique_types(resource_types)
    # Keep the historical type-only API resource-focused.  Typed callers use
    # ``retrieve_schema_for_graph`` and validate data-source kind explicitly.
    retrieved = [
        value for value in requested if provider_schema.resource_type_exists(value)
    ]
    missing = [value for value in requested if value not in retrieved]
    projection = {
        "schema_contract_version": SCHEMA_RAG_VERSION,
        "retrieval_method": "exact_resource_type_lookup_compatibility_mode",
        "resources": [
            {
                "instance_id": value,
                "type": value,
                "kind": "resource",
            }
            for value in retrieved
        ],
        "missing_types": missing,
        "negative_constraints": [
            f"Do not generate {value} because it is absent from the configured AWS provider schema."
            for value in missing
        ],
    }
    context = provider_schema.schema_context_for_types(retrieved)
    return SchemaRetrieval(
        requested_types=tuple(requested),
        retrieved_types=tuple(retrieved),
        missing_types=tuple(missing),
        context=context,
        schema_sha256=schema_sha256(),
        projection=projection,
    )


def retrieve_schema_for_graph(graph: dict[str, Any], prompt: str = "") -> SchemaRetrieval:
    requested = _unique_types(
        item.get("type", "")
        for item in graph.get("resources", [])
        if isinstance(item, dict) and item.get("kind") != "external_input"
    )
    projection = provider_schema.schema_projection_for_graph(graph, prompt)
    missing = _unique_types(projection.get("missing_types", []))
    retrieved = [value for value in requested if value not in missing]
    return SchemaRetrieval(
        requested_types=tuple(requested),
        retrieved_types=tuple(retrieved),
        missing_types=tuple(missing),
        context=provider_schema.render_schema_projection(projection),
        schema_sha256=schema_sha256(),
        projection=projection,
    )


def retrieval_note(retrieval: SchemaRetrieval) -> str:
    return json.dumps(retrieval.as_dict(), ensure_ascii=True, sort_keys=True)
