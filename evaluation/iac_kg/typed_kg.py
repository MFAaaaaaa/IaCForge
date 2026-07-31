"""Stable multi-granular KG entity/edge model and quality reporting."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any, Iterable

import provider_schema
import versioning


ENTITY_KINDS = {
    "ResourceType",
    "DataSourceType",
    "Argument",
    "NestedBlock",
    "ExportedAttribute",
    "Example",
    "ProviderVersion",
    "Service",
}
RELATIONS = {
    "HAS_ARGUMENT",
    "HAS_BLOCK",
    "EXPORTS",
    "HAS_EXAMPLE",
    "REFERENCES",
}


def entity_id(
    kind: str,
    type_name: str = "",
    member: str = "",
    provider_version=versioning.AWS_PROVIDER_VERSION,
):
    kind_token = {
        "ResourceType": "resource",
        "DataSourceType": "data_source",
        "Argument": "argument",
        "NestedBlock": "block",
        "ExportedAttribute": "attribute",
        "Example": "example",
        "ProviderVersion": "provider_version",
        "Service": "service",
    }[kind]
    parts = [f"aws@{provider_version}", kind_token]
    if type_name:
        parts.append(type_name)
    if member:
        parts.append(member)
    return "::".join(parts)


def _items(value):
    return [item for item in value or [] if isinstance(item, dict)]


def record_to_entities(record: dict[str, Any]):
    type_name = str(record.get("resource_type", ""))
    kind = (
        "DataSourceType"
        if str(record.get("kind", "")).replace(" ", "_") == "data_source"
        else "ResourceType"
    )
    root_id = entity_id(kind, type_name)
    yield {
        "id": root_id,
        "kind": kind,
        "type": type_name,
        "description": record.get("description", ""),
        "provider_version": versioning.AWS_PROVIDER_VERSION,
        "source_document": record.get("source_doc", ""),
    }
    for item in _items(record.get("required_args")) + _items(
        record.get("optional_args")
    ):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": entity_id("Argument", type_name, name),
                "kind": "Argument",
                "owner": root_id,
                "name": name,
                "required": item in _items(record.get("required_args")),
                "type_spec": item.get("type"),
            }
    for item in _items(record.get("attributes")):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": entity_id("ExportedAttribute", type_name, name),
                "kind": "ExportedAttribute",
                "owner": root_id,
                "name": name,
                "type_spec": item.get("type"),
            }
    for item in _items(record.get("blocks")):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": entity_id("NestedBlock", type_name, name),
                "kind": "NestedBlock",
                "owner": root_id,
                "name": name,
                "nesting_mode": item.get("nesting_mode", ""),
            }
    for index, item in enumerate(_items(record.get("examples"))):
        title = str(item.get("name") or f"example_{index + 1}")
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
        yield {
            "id": entity_id("Example", type_name, digest),
            "kind": "Example",
            "owner": root_id,
            "title": title,
            "source_document": record.get("source_doc", ""),
        }


def record_to_edges(record: dict[str, Any]):
    type_name = str(record.get("resource_type", ""))
    root_kind = (
        "DataSourceType"
        if str(record.get("kind", "")).replace(" ", "_") == "data_source"
        else "ResourceType"
    )
    root_id = entity_id(root_kind, type_name)
    for item in _items(record.get("required_args")) + _items(
        record.get("optional_args")
    ):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": f"has_argument:{root_id}->{name}",
                "source": root_id,
                "relation": "HAS_ARGUMENT",
                "target": entity_id("Argument", type_name, name),
            }
    for item in _items(record.get("attributes")):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": f"exports:{root_id}->{name}",
                "source": root_id,
                "relation": "EXPORTS",
                "target": entity_id("ExportedAttribute", type_name, name),
            }
    for item in _items(record.get("blocks")):
        name = str(item.get("name", ""))
        if name:
            yield {
                "id": f"has_block:{root_id}->{name}",
                "source": root_id,
                "relation": "HAS_BLOCK",
                "target": entity_id("NestedBlock", type_name, name),
            }
    for index, item in enumerate(_items(record.get("examples"))):
        title = str(item.get("name") or f"example_{index + 1}")
        digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
        yield {
            "id": f"has_example:{root_id}->{digest}",
            "source": root_id,
            "relation": "HAS_EXAMPLE",
            "target": entity_id("Example", type_name, digest),
        }


def normalize_reference_edge(edge: dict[str, Any]):
    source_type = str(edge.get("from") or edge.get("source_type") or "")
    target_type = str(edge.get("to") or edge.get("target_type") or "")
    source_path = str(edge.get("attribute") or edge.get("source_path") or "")
    target_path = str(edge.get("target_path") or "id")
    provenance = str(edge.get("provenance") or edge.get("source") or "unknown")
    confidence = edge.get("confidence")
    if confidence is None:
        confidence = 1.0 if provenance == "official_doc_example" else 0.55
    return {
        "edge_id": edge.get("edge_id")
        or f"ref:{source_type}.{source_path}->{target_type}.{target_path}",
        "source_type": source_type,
        "source_path": source_path,
        "target_type": target_type,
        "target_path": target_path,
        "relation": edge.get("relation") or "REQUIRES_VALUE_OF_TYPE",
        "provenance": provenance,
        "source_document": edge.get("source_document")
        or edge.get("source_doc", ""),
        "example_title": edge.get("example_title", ""),
        "support_count": int(edge.get("support_count", 1)),
        "confidence": float(confidence),
        "provider_version": edge.get(
            "provider_version", versioning.AWS_PROVIDER_VERSION
        ),
    }


def _load_jsonl(path: Path):
    if not path.exists():
        return []
    values = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                values.append(json.loads(line))
    return values


def _example_parseable(code):
    text = str(code or "")
    return (
        ('resource "' in text or 'data "' in text)
        and text.count("{") == text.count("}")
    )


def kg_quality_report(root: str | Path):
    root = Path(root)
    records = _load_jsonl(root / "resources.jsonl")
    raw_edges = _load_jsonl(root / "kg_edges.jsonl")
    edges = [normalize_reference_edge(item) for item in raw_edges]
    total = len(records)
    types_with_description = sum(
        bool(str(item.get("description", "")).strip()) for item in records
    )
    types_with_argument_docs = sum(
        bool(item.get("required_args") or item.get("optional_args"))
        for item in records
    )
    types_with_parseable_example = sum(
        any(_example_parseable(example.get("code")) for example in _items(item.get("examples")))
        for item in records
    )
    types_with_reference = len(
        {item["source_type"] for item in edges if item["source_type"]}
    )

    unmatched_doc_fields = 0
    schema_without_doc = 0
    for record in records:
        type_name = str(record.get("resource_type", ""))
        documented = {
            str(item.get("name", ""))
            for item in _items(record.get("required_args"))
            + _items(record.get("optional_args"))
            + _items(record.get("attributes"))
        }
        kind = (
            "data_source"
            if str(record.get("kind", "")).replace(" ", "_") == "data_source"
            else "resource"
        )
        schema_fields = provider_schema.supported_attributes(type_name, kind)
        unmatched_doc_fields += len(documented - schema_fields)
        schema_without_doc += len(schema_fields - documented)

    provenance_counts = {}
    for edge in edges:
        provenance = edge["provenance"]
        provenance_counts[provenance] = provenance_counts.get(provenance, 0) + 1
    return {
        "quality_report_version": "2.0",
        "provider_version": versioning.AWS_PROVIDER_VERSION,
        "resource_and_data_source_types": total,
        "description_coverage": types_with_description / total if total else 0.0,
        "argument_documentation_coverage": (
            types_with_argument_docs / total if total else 0.0
        ),
        "parseable_hcl_example_coverage": (
            types_with_parseable_example / total if total else 0.0
        ),
        "reference_out_edge_coverage": (
            types_with_reference / total if total else 0.0
        ),
        "reference_edges": len(edges),
        "reference_provenance_counts": provenance_counts,
        "documented_fields_missing_from_schema": unmatched_doc_fields,
        "schema_fields_without_documentation": schema_without_doc,
        "manual_reference_audit": {
            "required_sample_size": 200,
            "metrics": ["precision", "cohens_kappa"],
            "status": "requires two independent human annotations",
        },
    }


def write_reference_audit_sample(root: str | Path, output: str | Path, size=200):
    edges = [
        normalize_reference_edge(item)
        for item in _load_jsonl(Path(root) / "kg_edges.jsonl")
    ]
    sample = random.Random(20260730).sample(edges, min(size, len(edges)))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in sample for key in row}) + [
        "annotator_1_valid",
        "annotator_2_valid",
        "notes",
    ]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sample)
    return output
