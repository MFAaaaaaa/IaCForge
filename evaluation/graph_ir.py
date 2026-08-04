"""Typed task Graph IR parsing, normalization, validation, and provenance.

Graph IR v2 is instance-level and field-level.  The parser accepts legacy v1
payloads for archived experiments, but every downstream stage receives only a
canonical v2 object.  Invalid model text is never forwarded to HCL generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


GRAPH_IR_VERSION = "2.0"
RESOURCE_TYPE_RE = re.compile(r"^aws_[A-Za-z0-9_]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESOURCE_ADDRESS_RE = re.compile(
    r"^(?P<type>aws_[A-Za-z0-9_]+)\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
NODE_KINDS = {"resource", "data_source", "external_input"}
BINDING_KINDS = {"attribute_reference", "literal", "external_input"}


@dataclass(frozen=True)
class GraphIRValidation:
    graph: dict[str, Any]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    normalization_actions: tuple[str, ...] = ()
    generation_failure: bool = False
    raw_text: str = ""

    @property
    def valid(self) -> bool:
        return not self.errors and not self.generation_failure


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def empty_graph_ir(note: str = "") -> dict[str, Any]:
    graph = {
        "graph_ir_version": GRAPH_IR_VERSION,
        "resources": [],
        "bindings": [],
        "constraints": [],
        "explicit_dependencies": [],
        "requirements": [],
        "notes": [],
    }
    if note:
        graph["notes"].append(note)
    return graph


def _json_candidates(text: str):
    """Yield decodable JSON object substrings in textual order."""

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, consumed = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield text[match.start() : match.start() + consumed]


def extract_json_object(text: str) -> str:
    """Extract the first valid JSON object, tolerating Markdown and prose."""

    text = str(text or "").strip()
    fenced = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.I)
    for candidate_text in [*fenced, text]:
        candidate = next(_json_candidates(candidate_text), "")
        if candidate:
            return candidate
    return ""


def parse_graph_ir(text: str) -> dict[str, Any]:
    payload = extract_json_object(text)
    if not payload:
        raise ValueError("Graph IR response does not contain a valid JSON object.")
    graph = json.loads(payload)
    if not isinstance(graph, dict):
        raise ValueError("Graph IR root must be a JSON object.")
    return graph


def _safe_identifier(value: Any, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_")
    if not value:
        value = fallback
    if value[0].isdigit():
        value = f"node_{value}"
    return value


def _address_to_id(address: str, address_map: dict[str, str]) -> str:
    if address in address_map:
        return address_map[address]
    match = RESOURCE_ADDRESS_RE.fullmatch(address)
    return match.group("name") if match else address


def _normalize_legacy_graph(
    graph: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Convert legacy resource/dependency IR into the canonical v2 shape."""

    actions: list[str] = []
    warnings: list[str] = []
    resources = graph.get("resources", [])
    if not isinstance(resources, list):
        resources = []
    normalized_resources: list[dict[str, Any]] = []
    address_map: dict[str, str] = {}
    legacy_resource_dependencies: list[tuple[str, str]] = []
    for index, item in enumerate(resources):
        if not isinstance(item, dict):
            continue
        resource_type = str(item.get("type", "")).strip()
        legacy_name = str(item.get("name", "")).strip()
        instance_id = _safe_identifier(item.get("id") or legacy_name, f"resource_{index + 1}")
        kind = str(item.get("kind") or "resource").strip()
        normalized = dict(item)
        normalized["id"] = instance_id
        normalized["type"] = resource_type
        normalized["kind"] = kind
        normalized["role"] = str(item.get("role") or item.get("reason") or "").strip()
        normalized.pop("name", None)
        legacy_depends = normalized.pop("depends_on", None)
        if legacy_depends:
            warnings.append(
                f"resources[{index}].depends_on was legacy resource-level metadata; "
                "it was converted to explicit_dependencies"
            )
            if isinstance(legacy_depends, list):
                legacy_resource_dependencies.extend(
                    (instance_id, str(target).strip())
                    for target in legacy_depends
                    if str(target).strip()
                )
        normalized_resources.append(normalized)
        if resource_type and legacy_name:
            address_map[f"{resource_type}.{legacy_name}"] = instance_id

    bindings = graph.get("bindings", [])
    if not isinstance(bindings, list):
        bindings = []
    explicit_dependencies = graph.get("explicit_dependencies", [])
    if not isinstance(explicit_dependencies, list):
        explicit_dependencies = []
    for source, target_address in legacy_resource_dependencies:
        explicit_dependencies.append(
            {
                "source": source,
                "target": _address_to_id(target_address, address_map),
                "reason": "legacy resource-level depends_on",
                "evidence_ids": [],
            }
        )
    legacy_dependencies = graph.get("dependencies", [])
    if isinstance(legacy_dependencies, list) and legacy_dependencies:
        actions.append("converted legacy dependencies to explicit_dependencies")
        for item in legacy_dependencies:
            if not isinstance(item, dict):
                continue
            source = _address_to_id(str(item.get("from", "")).strip(), address_map)
            target = _address_to_id(str(item.get("to", "")).strip(), address_map)
            explicit_dependencies.append(
                {
                    "source": source,
                    "target": target,
                    "reason": str(item.get("reason", "")).strip(),
                    "evidence_ids": list(item.get("evidence_ids", []) or []),
                }
            )

    constraints = graph.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = []
    normalized_constraints = []
    for index, item in enumerate(constraints):
        if isinstance(item, dict):
            normalized_constraints.append(item)
        elif isinstance(item, str) and item.strip():
            normalized_constraints.append(
                {
                    "id": f"constraint_{index + 1}",
                    "target": "",
                    "semantic_requirement": item.strip(),
                    "status": "unresolved",
                    "source_text": item.strip(),
                }
            )
            actions.append("converted free-text constraint to unresolved structured constraint")

    requirements = graph.get("requirements", [])
    if not isinstance(requirements, list):
        requirements = []
    legacy_coverage = graph.get("intent_coverage", [])
    if not requirements and isinstance(legacy_coverage, list):
        for index, text in enumerate(legacy_coverage):
            if str(text).strip():
                requirements.append(
                    {
                        "id": f"req_{index + 1}",
                        "text": str(text).strip(),
                        "implemented_by": [],
                    }
                )
        if requirements:
            actions.append("converted intent_coverage to requirements")

    notes = graph.get("notes", [])
    if not isinstance(notes, list):
        notes = []
        actions.append("normalized notes to a list")

    normalized = {
        "graph_ir_version": GRAPH_IR_VERSION,
        "resources": normalized_resources,
        "bindings": bindings,
        "constraints": normalized_constraints,
        "explicit_dependencies": explicit_dependencies,
        "requirements": requirements,
        "notes": notes,
    }
    if str(graph.get("graph_ir_version", "1.0")) != GRAPH_IR_VERSION:
        actions.append(f"upgraded Graph IR to {GRAPH_IR_VERSION}")
    return normalized, actions, warnings


def _validate_endpoint(
    endpoint: Any,
    label: str,
    node_ids: set[str],
    errors: list[str],
) -> tuple[str, str]:
    if not isinstance(endpoint, dict):
        errors.append(f"{label} must be an object")
        return "", ""
    resource = str(endpoint.get("resource", "")).strip()
    path = str(endpoint.get("path", "")).strip()
    if resource not in node_ids:
        errors.append(f"{label}.resource is not declared: {resource!r}")
    if not path or not all(IDENTIFIER_RE.fullmatch(part) for part in path.split(".")):
        errors.append(f"{label}.path is invalid: {path!r}")
    return resource, path


def validate_graph_ir(graph: dict[str, Any]) -> GraphIRValidation:
    normalized, actions, legacy_warnings = _normalize_legacy_graph(graph)
    errors: list[str] = []
    warnings: list[str] = list(legacy_warnings)
    resources = normalized["resources"]
    node_ids: set[str] = set()

    for index, item in enumerate(resources):
        if not isinstance(item, dict):
            errors.append(f"resources[{index}] must be an object")
            continue
        instance_id = str(item.get("id", "")).strip()
        kind = str(item.get("kind", "")).strip()
        resource_type = str(item.get("type", "")).strip()
        if not IDENTIFIER_RE.fullmatch(instance_id):
            errors.append(f"resources[{index}].id is not Terraform-safe: {instance_id!r}")
        if instance_id in node_ids:
            errors.append(f"duplicate resource address/id: {instance_id}")
        node_ids.add(instance_id)
        if kind not in NODE_KINDS:
            errors.append(f"resources[{index}].kind is invalid: {kind!r}")
        if kind in {"resource", "data_source"} and not RESOURCE_TYPE_RE.fullmatch(resource_type):
            errors.append(
                f"resources[{index}].type is not an AWS Terraform type: {resource_type!r}"
            )
        if kind == "external_input" and not str(item.get("value_type", "")).strip():
            warnings.append(f"external input {instance_id!r} has no value_type")

    for index, item in enumerate(normalized["bindings"]):
        if not isinstance(item, dict):
            errors.append(f"bindings[{index}] must be an object")
            continue
        kind = str(item.get("kind") or "attribute_reference")
        if kind not in BINDING_KINDS:
            errors.append(f"bindings[{index}].kind is invalid: {kind!r}")
        source, _ = _validate_endpoint(
            item.get("source"), f"bindings[{index}].source", node_ids, errors
        )
        target, _ = _validate_endpoint(
            item.get("target"), f"bindings[{index}].target", node_ids, errors
        )
        if source and source == target:
            warnings.append(f"bindings[{index}] references the same instance: {source}")

    for index, item in enumerate(normalized["explicit_dependencies"]):
        if not isinstance(item, dict):
            errors.append(f"explicit_dependencies[{index}] must be an object")
            continue
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        if source not in node_ids:
            errors.append(f"explicit_dependencies[{index}].source is not declared: {source!r}")
        if target not in node_ids:
            errors.append(f"explicit_dependencies[{index}].target is not declared: {target!r}")
        if source and source == target:
            errors.append(f"explicit_dependencies[{index}] is a self-loop: {source}")

    for index, item in enumerate(normalized["constraints"]):
        if not isinstance(item, dict):
            errors.append(f"constraints[{index}] must be an object")
            continue
        target = str(item.get("target", "")).strip()
        if target:
            instance_id = target.split(".", 1)[0]
            if instance_id not in node_ids:
                errors.append(f"constraints[{index}].target is not declared: {target!r}")
        elif not str(item.get("semantic_requirement", "")).strip():
            errors.append(f"constraints[{index}] needs target or semantic_requirement")

    for index, item in enumerate(normalized["requirements"]):
        if not isinstance(item, dict):
            errors.append(f"requirements[{index}] must be an object")
            continue
        if not str(item.get("id", "")).strip() or not str(item.get("text", "")).strip():
            errors.append(f"requirements[{index}] needs id and text")
        if not isinstance(item.get("implemented_by", []), list):
            errors.append(f"requirements[{index}].implemented_by must be a list")

    return GraphIRValidation(
        normalized,
        tuple(errors),
        tuple(warnings),
        tuple(actions),
    )


def parse_and_validate_graph_ir(text: str) -> GraphIRValidation:
    validation = validate_graph_ir(parse_graph_ir(text))
    return GraphIRValidation(
        validation.graph,
        validation.errors,
        validation.warnings,
        validation.normalization_actions,
        False,
        str(text or ""),
    )


def safe_parse_graph_ir(text: str) -> GraphIRValidation:
    """Return a validly shaped empty IR when model output cannot be recovered."""

    try:
        validation = parse_and_validate_graph_ir(text)
        if validation.errors:
            return GraphIRValidation(
                graph=empty_graph_ir(
                    "IR validation failed; downstream generation received an empty IR."
                ),
                errors=validation.errors,
                warnings=validation.warnings,
                normalization_actions=validation.normalization_actions
                + ("replaced structurally invalid IR with canonical empty IR",),
                generation_failure=True,
                raw_text=str(text or ""),
            )
        return validation
    except Exception as exc:
        graph = empty_graph_ir("IR generation failed; downstream generation received an empty IR.")
        return GraphIRValidation(
            graph=graph,
            errors=(str(exc),),
            warnings=(),
            normalization_actions=("replaced invalid model output with canonical empty IR",),
            generation_failure=True,
            raw_text=str(text or ""),
        )


def resource_types(graph: dict[str, Any]) -> list[str]:
    types: list[str] = []
    for item in graph.get("resources", []):
        if not isinstance(item, dict) or item.get("kind", "resource") == "external_input":
            continue
        resource_type = str(item.get("type", "")).strip()
        if RESOURCE_TYPE_RE.fullmatch(resource_type) and resource_type not in types:
            types.append(resource_type)
    return types


def render_graph_ir(graph: dict[str, Any]) -> str:
    return json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True)


def provenance(prompt: str, validation: GraphIRValidation) -> dict[str, Any]:
    raw_sha = hashlib.sha256(validation.raw_text.encode("utf-8")).hexdigest()
    return {
        "graph_ir_version": GRAPH_IR_VERSION,
        "prompt_sha256": prompt_sha256(prompt),
        "raw_ir_sha256": raw_sha,
        "valid": validation.valid,
        "ir_generation_failure": validation.generation_failure,
        "errors": list(validation.errors),
        "warnings": list(validation.warnings),
        "normalization_actions": list(validation.normalization_actions),
        "resource_types": resource_types(validation.graph),
        "raw_ir": validation.raw_text,
        "normalized_ir": validation.graph,
    }
