"""Build the typed Full KG contract consumed by HCL generation."""

from __future__ import annotations

import json
import re
from typing import Any

import provider_schema


def _parse_evidence(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("KG evidence root must be an object.")
    return parsed


def _sorted(values, limit=18):
    items = []
    for value in values or []:
        value = str(value or "").strip()
        if value and value not in items:
            items.append(value)
    return sorted(items)[:limit]


def _resource_index(graph):
    by_type = {}
    for resource in graph.get("resources", []) or []:
        if not isinstance(resource, dict) or resource.get("kind") != "resource":
            continue
        resource_type = str(resource.get("type", "")).strip()
        instance_id = str(resource.get("id", "")).strip()
        if not resource_type or not instance_id:
            continue
        entry = by_type.setdefault(
            resource_type,
            {"addresses": [], "names": [], "source_spans": [], "evidence_ids": []},
        )
        address = f"{resource_type}.{instance_id}"
        if address not in entry["addresses"]:
            entry["addresses"].append(address)
        if instance_id not in entry["names"]:
            entry["names"].append(instance_id)
        source_span = str(resource.get("source_span", "")).strip()
        if source_span and source_span not in entry["source_spans"]:
            entry["source_spans"].append(source_span)
        for evidence_id in resource.get("evidence_ids", []) or []:
            evidence_id = str(evidence_id).strip()
            if evidence_id and evidence_id not in entry["evidence_ids"]:
                entry["evidence_ids"].append(evidence_id)
    return by_type


def _kg_candidates(evidence):
    candidates = {}
    for candidate in evidence.get("candidate_resources", []) or []:
        if not isinstance(candidate, dict):
            continue
        resource_type = str(candidate.get("type", "")).strip()
        if resource_type and resource_type not in candidates:
            candidates[resource_type] = candidate
    return candidates


def _high_confidence_types(evidence):
    resource_types = []
    for hint in evidence.get("required_resource_hints", []) or []:
        if not isinstance(hint, dict):
            continue
        if str(hint.get("confidence", "")).strip().lower() != "high":
            continue
        resource_type = str(hint.get("resource_type", "")).strip()
        if resource_type and resource_type not in resource_types:
            resource_types.append(resource_type)
    return resource_types


def _nested_blocks(resource_type, evidence, needed_blocks=None):
    needed_blocks = set(needed_blocks or [])
    blocks = []
    for block_name, block_spec in sorted(
        provider_schema.nested_block_types(resource_type).items()
    ):
        min_items = block_spec.get("min_items") or 0
        required_by_schema = bool(min_items and int(min_items) > 0)
        if not required_by_schema and block_name not in needed_blocks:
            continue
        blocks.append(
            {
                "block": block_name,
                "required_by_schema": required_by_schema,
                "required_attrs_when_used": _sorted(
                    provider_schema.nested_block_required_attributes(
                        resource_type, block_name
                    )
                ),
                "known_attrs": _sorted(
                    provider_schema.nested_block_attributes(resource_type, block_name)
                ),
                "source": "Terraform AWS provider schema",
                "syntax_rule": f"use nested block syntax: {block_name} {{ ... }}",
                "forbidden_argument_forms": [
                    f"{block_name} = ...",
                    f"{block_name}s = ...",
                ],
                "usage_policy": (
                    "must_emit"
                    if required_by_schema
                    else "emit_only_if_visible_prompt_or_dependency_requires"
                ),
            }
        )
    for hint in evidence.get("nested_block_hints", []) or []:
        if not isinstance(hint, dict):
            continue
        if str(hint.get("resource_type", "")).strip() != resource_type:
            continue
        block_name = str(hint.get("block", "")).strip()
        if not block_name or any(item["block"] == block_name for item in blocks):
            continue
        blocks.append(
            {
                "block": block_name,
                "required_by_schema": False,
                "required_attrs_when_used": _sorted(hint.get("required_attrs", [])),
                "known_attrs": _sorted(hint.get("known_attrs", [])),
                "evidence_id": hint.get("evidence_id", ""),
                "source": "Full KG nested block hint",
                "syntax_rule": f"use nested block syntax: {block_name} {{ ... }}",
                "forbidden_argument_forms": [
                    f"{block_name} = ...",
                    f"{block_name}s = ...",
                ],
                "usage_policy": "emit_only_if_visible_prompt_or_dependency_requires",
            }
        )
    return blocks[:12]


def _prompt_semantic_slots(prompt):
    text = str(prompt or "")
    lower = text.lower()
    slots = {
        "quoted_literals": [],
        "cidr_blocks": [],
        "regions": [],
        "availability_zones": [],
        "ports": [],
        "iam_actions": [],
        "dns_names": [],
        "record_types": [],
        "protocols": [],
        "cloud_concepts": [],
    }

    def add(slot, value):
        value = str(value or "").strip()
        if value and value not in slots[slot]:
            slots[slot].append(value)

    for value in re.findall(r'"([^"]{1,160})"', text):
        add("quoted_literals", value)
    for value in re.findall(r"'([^']{1,160})'", text):
        add("quoted_literals", value)
    for value in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b", text):
        add("cidr_blocks", value)
    for value in re.findall(r"\b[a-z]{2}-[a-z]+-\d\b", lower):
        add("regions", value)
    for value in re.findall(r"\b[a-z]{2}-[a-z]+-\d[a-z]\b", lower):
        add("availability_zones", value)
    for value in re.findall(r"\b(?:port|ports?)\s+(\d{1,5})\b", lower):
        add("ports", value)
    for value in re.findall(
        r"\b[A-Za-z0-9]+:[A-Za-z0-9*]+(?:[A-Za-z0-9*:/_-]*)\b", text
    ):
        if not value.startswith(("http:", "https:")):
            add("iam_actions", value)
    for value in re.findall(
        r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", lower
    ):
        add("dns_names", value)
    for value in re.findall(r"\b(A|AAAA|CNAME|MX|NS|PTR|SOA|SRV|TXT|CAA)\b", text):
        add("record_types", value)
    for value in re.findall(r"\b(HTTP|HTTPS|TCP|UDP|TLS|SSL|SSH)\b", text, flags=re.I):
        add("protocols", value.upper())
    for phrase in (
        "private hosted zone",
        "public hosted zone",
        "query logging",
        "weighted routing",
        "latency routing",
        "failover routing",
        "geolocation routing",
        "alias record",
        "load balancer",
        "server-side encryption",
        "versioning",
        "lifecycle rule",
        "public access block",
        "vpc",
        "subnet",
        "security group",
        "iam role",
        "iam policy",
        "cloudwatch log",
        "autoscaling",
        "elastic beanstalk",
        "rds",
        "route 53",
        "s3 bucket",
    ):
        if phrase in lower:
            add("cloud_concepts", phrase)
    return {key: values[:16] for key, values in slots.items() if values}


def build_provider_contract(prompt, normalized_ir, schema_projection, kg_evidence):
    """Build the typed resource/dependency contract used by Full KG runs."""

    del schema_projection  # Exact schema facts are read through provider_schema.
    evidence = _parse_evidence(kg_evidence)
    retrieved_contract = evidence.get("provider_contract", {})
    if not isinstance(retrieved_contract, dict):
        retrieved_contract = {}
    resource_index = _resource_index(normalized_ir)
    candidates = _kg_candidates(evidence)
    graph_types = list(resource_index)
    contract_types = list(graph_types)
    for resource_type in _high_confidence_types(evidence):
        if resource_type not in contract_types:
            contract_types.append(resource_type)

    needed_blocks = {}
    for dependency in evidence.get("dependency_hints", []) or []:
        if not isinstance(dependency, dict):
            continue
        from_type = str(dependency.get("from_type", "")).strip()
        attribute = str(dependency.get("attr", "")).strip()
        if "." in attribute and from_type:
            block_name = attribute.split(".", 1)[0]
            if block_name in provider_schema.nested_block_types(from_type):
                needed_blocks.setdefault(from_type, set()).add(block_name)

    resource_contracts = []
    for resource_type in contract_types[:16]:
        if not provider_schema.resource_type_exists(resource_type):
            continue
        candidate = candidates.get(resource_type, {})
        graph_entry = resource_index.get(resource_type, {})
        assignable = provider_schema.assignable_attributes(resource_type)
        resource_contracts.append(
            {
                "type": resource_type,
                "required_by_graph": resource_type in graph_types,
                "kg_retrieval_role": candidate.get("retrieval_role", "absent"),
                "graph_addresses": graph_entry.get("addresses", [])[:6],
                "graph_names": graph_entry.get("names", [])[:6],
                "source_spans": graph_entry.get("source_spans", [])[:4],
                "required_attributes": _sorted(
                    set(provider_schema.required_attributes(resource_type))
                    | set(candidate.get("required_attrs", []))
                ),
                "useful_optional_attributes": [
                    attribute
                    for attribute in _sorted(candidate.get("useful_optional_attrs", []))
                    if attribute in assignable
                ][:12],
                "forbidden_computed_attributes": _sorted(
                    set(provider_schema.computed_only_attributes(resource_type))
                    | set(candidate.get("computed_only_attrs", []))
                ),
                "nested_block_contracts": _nested_blocks(
                    resource_type, evidence, needed_blocks.get(resource_type)
                ),
                "evidence_ids": _sorted(
                    graph_entry.get("evidence_ids", [])
                    + ([candidate.get("evidence_id")] if candidate else [])
                ),
                "generation_policy": (
                    "must_generate_resource"
                    if resource_type in graph_types
                    else "may_generate_only_if_visible_prompt_requires_or_dependency_closure_requires"
                ),
            }
        )

    dependency_contracts = []
    for dependency in evidence.get("dependency_hints", []) or []:
        if not isinstance(dependency, dict):
            continue
        from_type = str(dependency.get("from_type", "")).strip()
        to_type = str(dependency.get("to_type", "")).strip()
        attribute = str(dependency.get("attr", "")).strip()
        if not from_type or not to_type or not attribute:
            continue
        attribute_root = attribute.split(".", 1)[0]
        if not provider_schema.resource_type_exists(from_type):
            continue
        if attribute_root not in provider_schema.assignable_attributes(
            from_type
        ) and attribute_root not in provider_schema.nested_block_types(from_type):
            continue
        dependency_contracts.append(
            {
                "from_type": from_type,
                "to_type": to_type,
                "attribute": attribute,
                "expression_hint": str(dependency.get("expr_hint", "")).strip(),
                "evidence_id": str(dependency.get("evidence_id", "")).strip(),
                "use_when": "both endpoint resource types are generated",
                "reference_policy": "replace placeholder names with generated Terraform labels",
                "type_fidelity_policy": "preserve the exact endpoint resource types",
            }
        )

    return {
        "contract_kind": "typed_provider_contract",
        "source_policy": (
            "Built only from the visible Prompt, prompt-derived Graph IR, bundled "
            "provider schema, and prompt-retrieved Full KG evidence."
        ),
        "retrieved_evidence_kind": evidence.get(
            "evidence_kind", "full_kg_evidence"
        ),
        "retrieved_contract_kind": evidence.get("contract_kind", ""),
        "resource_contracts": resource_contracts,
        "dependency_contracts": dependency_contracts[:24],
        "literal_attribute_obligations": [],
        "prompt_semantic_slots": _prompt_semantic_slots(prompt),
        "retrieved_prompt_semantic_slots": retrieved_contract.get(
            "prompt_semantic_slots", {}
        ),
        "value_bindings": retrieved_contract.get("value_bindings", [])[:24],
        "semantic_obligations": retrieved_contract.get("semantic_obligations", [])[:24],
        "usage_constraints": retrieved_contract.get("usage_constraints", [])[:16],
        "negative_constraints": retrieved_contract.get("negative_constraints", [])[:12],
        "global_generation_rules": [
            "Generate every resource with generation_policy=must_generate_resource.",
            "Use may_generate resources only when required by the visible Prompt or dependency closure.",
            "Include every required attribute for each generated resource.",
            "Never assign forbidden computed attributes.",
            "Use nested block contracts with block syntax.",
            "Use dependency contracts when both endpoint resource types are generated.",
            "Preserve exact resource types in dependency contracts.",
            "Apply value bindings to generated target resources and attributes.",
            "Treat usage and negative constraints as provider-syntax guardrails.",
            "Preserve visible literal values captured in prompt semantic slots.",
            "Do not introduce input variables without realistic defaults.",
        ],
    }


def render_provider_contract(contract):
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True)
