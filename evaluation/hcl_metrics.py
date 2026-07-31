"""Deterministic pre-Terraform structural metrics for generated HCL."""

from __future__ import annotations

import re
from typing import Any


BLOCK_START_RE = re.compile(
    r'\b(?P<kind>resource|data)\s+"(?P<type>aws_[A-Za-z0-9_]+)"\s+'
    r'"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"\s*\{'
)
ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^\n#]+)"
)


def _matching_brace(text, opening):
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def parse_top_level_blocks(hcl: str):
    blocks = []
    for match in BLOCK_START_RE.finditer(str(hcl or "")):
        closing = _matching_brace(hcl, match.end() - 1)
        if closing < 0:
            continue
        body = hcl[match.end() : closing]
        blocks.append(
            {
                "kind": "data_source" if match.group("kind") == "data" else "resource",
                "type": match.group("type"),
                "name": match.group("name"),
                "address": (
                    f"data.{match.group('type')}.{match.group('name')}"
                    if match.group("kind") == "data"
                    else f"{match.group('type')}.{match.group('name')}"
                ),
                "body": body,
                "assignments": {
                    item.group("name"): item.group("value").strip()
                    for item in ASSIGNMENT_RE.finditer(body)
                },
            }
        )
    return blocks


def analyze_hcl(
    hcl: str,
    graph_ir: dict[str, Any],
    provider_contract: dict[str, Any] | None = None,
):
    blocks = parse_top_level_blocks(hcl)
    actual = {
        (item["kind"], item["type"], item["name"]): item for item in blocks
    }
    expected = {
        (
            str(item.get("kind", "resource")),
            str(item.get("type", "")),
            str(item.get("id", "")),
        )
        for item in graph_ir.get("resources", [])
        if isinstance(item, dict) and item.get("kind") != "external_input"
    }
    missing = sorted(expected - set(actual))
    extra = sorted(set(actual) - expected)

    binding_results = []
    for binding in graph_ir.get("bindings", []):
        if not isinstance(binding, dict):
            continue
        source = binding.get("source", {})
        target = binding.get("target", {})
        source_id = str(source.get("resource", ""))
        source_path = str(source.get("path", ""))
        target_id = str(target.get("resource", ""))
        target_path = str(target.get("path", ""))
        source_instance = next(
            (
                item
                for item in graph_ir.get("resources", [])
                if isinstance(item, dict) and item.get("id") == source_id
            ),
            {},
        )
        target_instance = next(
            (
                item
                for item in graph_ir.get("resources", [])
                if isinstance(item, dict) and item.get("id") == target_id
            ),
            {},
        )
        source_key = (
            source_instance.get("kind", "resource"),
            source_instance.get("type", ""),
            source_id,
        )
        target_prefix = (
            f"data.{target_instance.get('type')}.{target_id}"
            if target_instance.get("kind") == "data_source"
            else f"{target_instance.get('type')}.{target_id}"
        )
        expected_expression = f"{target_prefix}.{target_path}"
        source_block = actual.get(source_key, {})
        if "." in source_path:
            nested_attr = source_path.rsplit(".", 1)[-1]
            body = source_block.get("body", "")
            realized = bool(
                re.search(
                    rf"\b{re.escape(nested_attr)}\s*=\s*[^\n]*{re.escape(expected_expression)}",
                    body,
                )
            )
        else:
            assignment = source_block.get("assignments", {}).get(source_path, "")
            realized = expected_expression in assignment
        binding_results.append(
            {
                "source": f"{source_id}.{source_path}",
                "target": f"{target_id}.{target_path}",
                "expected_expression": expected_expression,
                "realized": realized,
            }
        )

    computed_assignments = []
    unsupported_assignments = []
    missing_required = []
    contracts = (provider_contract or {}).get("instance_contracts", {})
    for instance_id, contract in contracts.items():
        key = (contract.get("kind"), contract.get("type"), instance_id)
        assignments = set(actual.get(key, {}).get("assignments", {}))
        forbidden = set(contract.get("forbidden_assignments", []))
        allowed = set(contract.get("allowed_assignments", []))
        required = set(contract.get("required_assignments", []))
        computed_assignments.extend(
            f"{instance_id}.{name}" for name in sorted(assignments & forbidden)
        )
        unsupported_assignments.extend(
            f"{instance_id}.{name}" for name in sorted(assignments - allowed)
        )
        missing_required.extend(
            f"{instance_id}.{name}" for name in sorted(required - assignments)
        )

    return {
        "resource_count": sum(item["kind"] == "resource" for item in blocks),
        "data_source_count": sum(item["kind"] == "data_source" for item in blocks),
        "ir_node_count": len(expected),
        "ir_node_realized": len(expected) - len(missing),
        "ir_binding_count": len(binding_results),
        "ir_binding_realized": sum(item["realized"] for item in binding_results),
        "binding_details": binding_results,
        "extra_resources": [".".join(item) for item in extra],
        "missing_ir_resources": [".".join(item) for item in missing],
        "computed_only_assignments": computed_assignments,
        "unsupported_assignments": unsupported_assignments,
        "missing_required_assignments": missing_required,
    }
