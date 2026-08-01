"""Compile Graph IR, exact schema and KG references into a canonical contract."""

from __future__ import annotations

import json
from typing import Any

import evidence_projection
import provider_schema
import versioning


def _instances_by_id(graph):
    return {
        str(item.get("id", "")): item
        for item in graph.get("resources", [])
        if isinstance(item, dict)
    }


def _terraform_address(instance):
    instance_id = str(instance.get("id", ""))
    kind = str(instance.get("kind", "resource"))
    type_name = str(instance.get("type", ""))
    if kind == "data_source":
        return f"data.{type_name}.{instance_id}"
    if kind == "external_input":
        return f"var.{instance_id}"
    return f"{type_name}.{instance_id}"


def _reference_expression(instance, path):
    address = _terraform_address(instance)
    return f"{address}.{path}" if path else address


def _schema_by_instance(schema_projection):
    return {
        str(item.get("instance_id", "")): item
        for item in schema_projection.get("resources", [])
        if isinstance(item, dict)
    }


def _constraint_assignments(graph):
    values = {}
    unresolved = []
    for item in graph.get("constraints", []):
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", ""))
        if target and "." in target and "value" in item:
            instance_id, path = target.split(".", 1)
            values.setdefault(instance_id, {})[path] = {
                "kind": "literal",
                "value": item.get("value"),
                "operator": item.get("operator", "equals"),
                "source_text": item.get("source_text", ""),
            }
        else:
            unresolved.append(item)
    return values, unresolved


def _prompt_value_assignments(evidence, instances):
    assignments = {}
    value_bindings = (
        evidence.get("provider_contract", {}).get("value_bindings", []) or []
    )
    for item in value_bindings:
        if not isinstance(item, dict):
            continue
        target_type = str(item.get("target_type", ""))
        attribute = str(item.get("attribute", ""))
        if "/" in attribute:
            paths = attribute.split("/")
        else:
            paths = [attribute]
        matches = [
            instance_id
            for instance_id, instance in instances.items()
            if instance.get("type") == target_type
        ]
        if len(matches) != 1:
            continue
        for path in paths:
            if path and provider_schema.is_assignable(target_type, path):
                assignments.setdefault(matches[0], {})[path] = {
                    "kind": "literal",
                    "value": item.get("value"),
                    "source": "visible_prompt_slot",
                }
    return assignments


def _binding_contract(binding, instances):
    source = binding.get("source", {})
    target = binding.get("target", {})
    source_id = str(source.get("resource", ""))
    target_id = str(target.get("resource", ""))
    source_path = str(source.get("path", ""))
    target_path = str(target.get("path", ""))
    target_instance = instances.get(target_id, {})
    consumer_assignment = f"{source_id}.{source_path}"
    producer_reference = f"{target_id}.{target_path}"
    return {
        "id": binding.get("id")
        or f"binding:{source_id}.{source_path}->{target_id}.{target_path}",
        # source/target are retained for archived metrics. The explicit names
        # prevent small compilers from reversing the assignment direction.
        "source": consumer_assignment,
        "target": producer_reference,
        "consumer_assignment": consumer_assignment,
        "producer_reference": producer_reference,
        "kind": binding.get("kind", "attribute_reference"),
        "expression": _reference_expression(target_instance, target_path),
        "evidence_id": binding.get("evidence_id", ""),
    }


def _instantiate_kg_bindings(evidence, graph, existing):
    instances = _instances_by_id(graph)
    existing_pairs = {
        (item.get("source", ""), item.get("target", "")) for item in existing
    }
    templates = (
        evidence.get("provider_contract", {}).get("dependency_templates", []) or []
    )
    generated = []
    for template in templates:
        if not isinstance(template, dict):
            continue
        confidence = float(template.get("confidence", 0.0))
        provenance = template.get("provenance") or template.get("source_kind")
        # Name/schema hints are recall aids, not hard compiler contracts.
        if confidence < 0.8 or provenance in {
            "provider_schema_name_rule",
            "schema_name_hint",
        }:
            continue
        sources = [
            (instance_id, value)
            for instance_id, value in instances.items()
            if value.get("type") == template.get("from_type")
        ]
        targets = [
            (instance_id, value)
            for instance_id, value in instances.items()
            if value.get("type") == template.get("to_type")
        ]
        if len(sources) != 1 or len(targets) != 1 or not template.get("attr"):
            continue
        source_id, _ = sources[0]
        target_id, target_instance = targets[0]
        source_path = str(template.get("attr"))
        target_path = str(template.get("target_path") or "id")
        source_type = str(template.get("from_type", ""))
        target_type = str(template.get("to_type", ""))
        # Type-level KG edges are compiler bindings only when the configured
        # provider schema proves both endpoint roles and value compatibility.
        if not provider_schema.is_assignable(source_type, source_path, "resource"):
            continue
        if not provider_schema.is_exported(target_type, target_path, "resource"):
            continue
        source_spec = provider_schema.attribute_type(
            source_type, source_path.split(".", 1)[0], "resource"
        )
        target_spec = provider_schema.attribute_type(
            target_type, target_path.split(".", 1)[0], "resource"
        )
        if not provider_schema.types_compatible(source_spec, target_spec):
            continue
        pair = (f"{source_id}.{source_path}", f"{target_id}.{target_path}")
        if pair in existing_pairs:
            continue
        generated.append(
            {
                "id": template.get("edge_id")
                or template.get("evidence_id")
                or f"binding:{pair[0]}->{pair[1]}",
                "source": pair[0],
                "target": pair[1],
                "consumer_assignment": pair[0],
                "producer_reference": pair[1],
                "kind": "attribute_reference",
                "expression": _reference_expression(target_instance, target_path),
                "evidence_id": template.get("evidence_id", ""),
                "provenance": provenance,
                "confidence": confidence,
                "inferred_from_type_level_kg": True,
            }
        )
        existing_pairs.add(pair)
    return generated


def build_provider_contract(
    prompt: str,
    normalized_ir: dict[str, Any],
    schema_projection: dict[str, Any],
    kg_evidence: str | dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the only canonical compiler contract consumed by HCL generation."""

    evidence = evidence_projection.parse_evidence(kg_evidence)
    instances = _instances_by_id(normalized_ir)
    schema_contracts = _schema_by_instance(schema_projection)
    constraint_values, unresolved_constraints = _constraint_assignments(normalized_ir)
    prompt_values = _prompt_value_assignments(evidence, instances)
    ir_bindings = [
        _binding_contract(item, instances)
        for item in normalized_ir.get("bindings", [])
        if isinstance(item, dict)
    ]
    bindings = ir_bindings + _instantiate_kg_bindings(
        evidence, normalized_ir, ir_bindings
    )

    binding_assignments = {}
    for item in bindings:
        source = str(item.get("source", ""))
        if "." not in source:
            continue
        instance_id, path = source.split(".", 1)
        binding_assignments.setdefault(instance_id, {})[path] = {
            "kind": "reference",
            "expression": item.get("expression", ""),
            "binding_id": item.get("id", ""),
        }

    instance_contracts = {}
    resources = []
    for instance_id, instance in instances.items():
        kind = str(instance.get("kind", "resource"))
        type_name = str(instance.get("type", ""))
        if kind == "external_input":
            resources.append(
                {
                    "instance_id": instance_id,
                    "kind": kind,
                    "value_type": instance.get("value_type", "string"),
                }
            )
            continue
        schema = schema_contracts.get(instance_id, {})
        must_assign = dict(binding_assignments.get(instance_id, {}))
        should_assign = {}
        should_assign.update(prompt_values.get(instance_id, {}))
        should_assign.update(constraint_values.get(instance_id, {}))
        required = list(schema.get("required_args", []))
        missing_required = [
            path for path in required if path not in must_assign and path not in should_assign
        ]
        nested_blocks = list(schema.get("nested_blocks", []))
        nested_block_names = {
            str(item.get("name", ""))
            for item in nested_blocks
            if isinstance(item, dict)
        }
        instance_contract = {
            "type": type_name,
            "kind": kind,
            "generation_policy": (
                "must_generate_block"
                if kind in {"resource", "data_source"}
                else "declare_external_input"
            ),
            "required_assignments": required,
            "allowed_assignments": sorted(
                set(required)
                | set(schema.get("relevant_optional_args", []))
                | nested_block_names
            ),
            "must_assign": must_assign,
            "should_assign": should_assign,
            "unresolved_required_assignments": missing_required,
            "forbidden_assignments": list(schema.get("all_computed_attrs", [])),
            "nested_blocks": nested_blocks,
            "arg_types": dict(schema.get("arg_types", {})),
        }
        instance_contracts[instance_id] = instance_contract
        resources.append(
            {
                "instance_id": instance_id,
                "type": type_name,
                "kind": kind,
                "required_assignments": required,
                "allowed_assignments": instance_contract["allowed_assignments"],
                "forbidden_assignments": instance_contract["forbidden_assignments"],
                "nested_blocks": instance_contract["nested_blocks"],
            }
        )

    evidence_contract = evidence.get("provider_contract", {})
    negative = list(schema_projection.get("negative_constraints", []))
    negative.extend(evidence_contract.get("negative_constraints", []) or [])
    contract = {
        "contract_version": versioning.CONTRACT_VERSION,
        "resources": resources,
        "instance_contracts": instance_contracts,
        "bindings": bindings,
        "value_bindings": [
            item
            for values in prompt_values.values()
            for item in values.values()
        ],
        "usage_constraints": list(
            evidence_contract.get("usage_constraints", []) or []
        ),
        "negative_constraints": negative,
        "explicit_dependencies": list(
            normalized_ir.get("explicit_dependencies", []) or []
        ),
        "unresolved_constraints": unresolved_constraints,
        "unresolved_requirements": [
            item
            for item in normalized_ir.get("requirements", [])
            if isinstance(item, dict) and not item.get("implemented_by")
        ],
        "source_policy": (
            "Compiled only from the visible prompt, normalized Graph IR, exact "
            "version-aligned provider schema and public KG evidence."
        ),
    }
    contract["contract_sha256"] = versioning.canonical_sha256(contract)
    return contract


def validate_provider_contract(contract):
    violations = []
    for instance_id, item in contract.get("instance_contracts", {}).items():
        required = set(item.get("required_assignments", []))
        forbidden = set(item.get("forbidden_assignments", []))
        assigned = set(item.get("must_assign", [])) | set(item.get("should_assign", []))
        assigned_roots = {path.split(".", 1)[0] for path in assigned}
        overlap = assigned_roots & forbidden
        if overlap:
            violations.append(
                {
                    "code": "COMPUTED_ONLY_ASSIGNMENT",
                    "instance_id": instance_id,
                    "attributes": sorted(overlap),
                }
            )
        unsupported = assigned_roots - set(item.get("allowed_assignments", []))
        if unsupported:
            violations.append(
                {
                    "code": "UNSUPPORTED_ASSIGNMENT",
                    "instance_id": instance_id,
                    "attributes": sorted(unsupported),
                }
            )
        unresolved = required - assigned
        if unresolved != set(item.get("unresolved_required_assignments", [])):
            violations.append(
                {
                    "code": "INCONSISTENT_REQUIRED_ASSIGNMENTS",
                    "instance_id": instance_id,
                    "attributes": sorted(unresolved),
                }
            )
    return {"valid": not violations, "violations": violations}


def render_provider_contract(contract):
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True)


def build_hcl_skeleton(contract):
    """Create a deterministic block skeleton without inventing unknown values."""

    blocks = []
    for instance_id, item in contract.get("instance_contracts", {}).items():
        kind = item.get("kind", "resource")
        type_name = item.get("type", "")
        keyword = "data" if kind == "data_source" else "resource"
        lines = [f'{keyword} "{type_name}" "{instance_id}" {{']
        assigned = {}
        assigned.update(item.get("must_assign", {}))
        assigned.update(item.get("should_assign", {}))
        nested_assignments = {}
        for attr, assignment in sorted(assigned.items()):
            if "." in attr:
                block_name, nested_attr = attr.split(".", 1)
                nested_assignments.setdefault(block_name, {})[nested_attr] = assignment
                continue
            if assignment.get("kind") == "reference":
                value = assignment.get("expression", "")
            else:
                value = json.dumps(assignment.get("value"), ensure_ascii=False)
            lines.append(f"  {attr} = {value}")
        for block_name, block_values in sorted(nested_assignments.items()):
            lines.append(f"  {block_name} {{")
            for attr, assignment in sorted(block_values.items()):
                if assignment.get("kind") == "reference":
                    value = assignment.get("expression", "")
                else:
                    value = json.dumps(
                        assignment.get("value"), ensure_ascii=False
                    )
                lines.append(f"    {attr} = {value}")
            lines.append("  }")
        for attr in item.get("unresolved_required_assignments", []):
            lines.append(f"  # REQUIRED: assign {attr} from the visible prompt/contract")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
