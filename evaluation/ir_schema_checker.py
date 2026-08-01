"""Provider-schema consistency checking for typed Graph IR."""

from __future__ import annotations

import copy
import difflib
from dataclasses import dataclass
from typing import Any

import provider_schema


@dataclass(frozen=True)
class IRSchemaCheck:
    valid: bool
    graph: dict[str, Any]
    violations: tuple[dict[str, Any], ...]
    normalization_actions: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "violations": list(self.violations),
            "normalization_actions": list(self.normalization_actions),
        }


def _violation(code, path, message, suggested_path=""):
    value = {"code": code, "path": path, "message": message}
    if suggested_path:
        value["suggested_path"] = suggested_path
    return value


def _high_confidence_match(path, candidates):
    candidates = sorted(set(candidates))
    matches = difflib.get_close_matches(path, candidates, n=2, cutoff=0.82)
    if len(matches) == 1:
        return matches[0]
    direct = f"{path}_id"
    if direct in candidates:
        return direct
    return ""


def check_graph_ir(graph: dict[str, Any], apply_safe_repairs: bool = True) -> IRSchemaCheck:
    normalized = copy.deepcopy(graph)
    violations = []
    actions = []
    instances = {
        str(item.get("id", "")): item
        for item in normalized.get("resources", [])
        if isinstance(item, dict)
    }

    for instance_id, instance in instances.items():
        kind = str(instance.get("kind", "resource"))
        if kind == "external_input":
            continue
        type_name = str(instance.get("type", ""))
        if not provider_schema.type_exists(type_name, kind):
            actual_kind = provider_schema.type_kind(type_name)
            if actual_kind:
                violations.append(
                    _violation(
                        "WRONG_NODE_KIND",
                        f"resources.{instance_id}.kind",
                        f"{type_name} exists as {actual_kind}, not {kind}.",
                        actual_kind,
                    )
                )
            else:
                violations.append(
                    _violation(
                        "UNKNOWN_PROVIDER_TYPE",
                        f"resources.{instance_id}.type",
                        f"{type_name} is absent from the configured provider schema.",
                    )
                )

    for index, binding in enumerate(normalized.get("bindings", [])):
        if not isinstance(binding, dict):
            continue
        source = binding.get("source", {})
        target = binding.get("target", {})
        source_id = str(source.get("resource", ""))
        target_id = str(target.get("resource", ""))
        source_path = str(source.get("path", ""))
        target_path = str(target.get("path", ""))
        source_instance = instances.get(source_id, {})
        target_instance = instances.get(target_id, {})
        source_type = str(source_instance.get("type", ""))
        target_type = str(target_instance.get("type", ""))
        source_kind = str(source_instance.get("kind", "resource"))
        target_kind = str(target_instance.get("kind", "resource"))

        if source_instance.get("kind") != "external_input" and source_type:
            if not provider_schema.is_assignable(
                source_type, source_path, source_kind
            ):
                candidates = provider_schema.assignable_attributes(
                    source_type, source_kind
                )
                suggestion = _high_confidence_match(source_path, candidates)
                violations.append(
                    _violation(
                        "UNKNOWN_OR_UNASSIGNABLE_SOURCE_ARGUMENT",
                        f"bindings[{index}].source.path",
                        f"{source_type}.{source_path} is not assignable.",
                        suggestion,
                    )
                )
                if apply_safe_repairs and suggestion:
                    source["path"] = suggestion
                    actions.append(
                        {
                            "code": "SAFE_SOURCE_PATH_CORRECTION",
                            "from": source_path,
                            "to": suggestion,
                            "binding_index": index,
                        }
                    )
                    source_path = suggestion

        if target_instance.get("kind") != "external_input" and target_type:
            if not provider_schema.is_exported(
                target_type, target_path, target_kind
            ):
                candidates = provider_schema.supported_attributes(
                    target_type, target_kind
                )
                suggestion = _high_confidence_match(target_path, candidates)
                violations.append(
                    _violation(
                        "UNKNOWN_TARGET_ATTRIBUTE",
                        f"bindings[{index}].target.path",
                        f"{target_type}.{target_path} is not exported.",
                        suggestion,
                    )
                )
                if apply_safe_repairs and suggestion:
                    target["path"] = suggestion
                    actions.append(
                        {
                            "code": "SAFE_TARGET_PATH_CORRECTION",
                            "from": target_path,
                            "to": suggestion,
                            "binding_index": index,
                        }
                    )
                    target_path = suggestion

        if source_type and target_type and source_path and target_path:
            source_spec = provider_schema.attribute_type(
                source_type, source_path.split(".")[0], source_kind
            )
            target_spec = provider_schema.attribute_type(
                target_type, target_path.split(".")[0], target_kind
            )
            if not provider_schema.types_compatible(source_spec, target_spec):
                violations.append(
                    _violation(
                        "INCOMPATIBLE_BINDING_TYPES",
                        f"bindings[{index}]",
                        f"{source_type}.{source_path} and {target_type}.{target_path} have incompatible schema types.",
                    )
                )

    hard_codes = {
        "UNKNOWN_PROVIDER_TYPE",
        "WRONG_NODE_KIND",
        "UNKNOWN_OR_UNASSIGNABLE_SOURCE_ARGUMENT",
        "UNKNOWN_TARGET_ATTRIBUTE",
        "INCOMPATIBLE_BINDING_TYPES",
    }
    repair_code_for_violation = {
        "UNKNOWN_OR_UNASSIGNABLE_SOURCE_ARGUMENT": "SAFE_SOURCE_PATH_CORRECTION",
        "UNKNOWN_TARGET_ATTRIBUTE": "SAFE_TARGET_PATH_CORRECTION",
    }
    unresolved = []
    for item in violations:
        if item["code"] not in hard_codes:
            continue
        expected_repair = repair_code_for_violation.get(item["code"], "")
        repaired = bool(expected_repair) and any(
            action.get("code") == expected_repair
            and action.get("binding_index") == _binding_index(item.get("path", ""))
            for action in actions
        )
        if not repaired:
            unresolved.append(item)
    return IRSchemaCheck(not unresolved, normalized, tuple(violations), tuple(actions))


def salvage_by_dropping_invalid_bindings(check: IRSchemaCheck) -> IRSchemaCheck:
    """Keep valid provider nodes while removing only schema-invalid bindings.

    Salvage is deliberately unavailable when any violation belongs to a node
    rather than a binding. The reduced graph is checked again from scratch.
    """
    if check.valid or not check.violations:
        return check
    binding_indices = {_binding_index(item.get("path", "")) for item in check.violations}
    if None in binding_indices or not binding_indices:
        return check

    graph = copy.deepcopy(check.graph)
    bindings = graph.get("bindings", [])
    graph["bindings"] = [
        binding for index, binding in enumerate(bindings) if index not in binding_indices
    ]
    rechecked = check_graph_ir(graph)
    if not rechecked.valid:
        return check
    actions = list(check.normalization_actions)
    actions.extend(
        {
            "code": "DROP_SCHEMA_INVALID_BINDING",
            "binding_index": index,
            "reason": "binding failed provider-schema consistency checking",
        }
        for index in sorted(binding_indices)
    )
    actions.extend(rechecked.normalization_actions)
    return IRSchemaCheck(True, rechecked.graph, check.violations, tuple(actions))


def _binding_index(path: str):
    if not path.startswith("bindings["):
        return None
    try:
        return int(path.split("[", 1)[1].split("]", 1)[0])
    except (ValueError, IndexError):
        return None
