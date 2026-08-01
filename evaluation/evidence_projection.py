"""Stage-specific projections over the full prompt-retrieved KG evidence."""

from __future__ import annotations

import json
import os
from typing import Any


PLANNER_EVIDENCE_VERSION = "2.1"


def parse_evidence(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("KG evidence root must be an object.")
    return parsed


def _relation_for_dependency(item):
    return str(
        item.get("relation")
        or item.get("dependency_semantics")
        or "REQUIRES_VALUE_OF_TYPE"
    )


def project_planner_evidence(
    full_evidence: str | dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep only coarse resource-selection and dependency-planning evidence."""

    evidence = parse_evidence(full_evidence)
    candidates = []
    for item in evidence.get("candidate_resources", []):
        if not isinstance(item, dict) or not item.get("type"):
            continue
        evidence_ids = item.get("evidence_ids") or [item.get("evidence_id", "")]
        candidates.append(
            {
                "type": item["type"],
                "purpose": item.get("purpose") or item.get("reason") or "",
                "score": float(item.get("score", 0.0)),
                "matched_by": list(item.get("matched_by", []) or []),
                "evidence_ids": [value for value in evidence_ids if value],
            }
        )

    # Small planners degrade when the projection repeats a long, low-precision
    # tail. The full evidence remains available to the deterministic contract
    # stage; the planner sees only the strongest resource-selection evidence.
    max_candidates = max(1, int(os.environ.get("IAC_PLANNER_MAX_CANDIDATES", "8")))
    candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)[
        :max_candidates
    ]
    candidate_types = {item["type"] for item in candidates}

    dependencies = []
    for item in evidence.get("dependency_hints", []):
        if not isinstance(item, dict):
            continue
        # Keep an edge when at least one endpoint survived candidate pruning:
        # the other endpoint may be precisely the missing prerequisite that
        # graph planning needs to recover.
        if (
            item.get("from_type") not in candidate_types
            and item.get("to_type") not in candidate_types
        ):
            continue
        dependencies.append(
            {
                "from_type": item.get("from_type", ""),
                "to_type": item.get("to_type", ""),
                "relation": _relation_for_dependency(item),
                "source_field": item.get("source_path") or item.get("attr") or "",
                "target_field": item.get("target_path") or item.get("target_attr") or "id",
                "confidence": float(item.get("confidence", 0.0)),
                "provenance": item.get("provenance") or item.get("source_kind") or "",
                "evidence_id": item.get("evidence_id") or item.get("edge_id") or "",
            }
        )

    alternatives = []
    for candidate in candidates:
        resource_type = candidate["type"]
        if resource_type.startswith("aws_default_"):
            alternatives.append(
                {
                    "candidate": resource_type,
                    "warning": (
                        "This type manages an existing default AWS object; use it only "
                        "when the prompt explicitly requests the default object."
                    ),
                }
            )
    return {
        "planner_evidence_version": PLANNER_EVIDENCE_VERSION,
        "candidate_resources": candidates,
        "dependency_candidates": dependencies,
        "required_resource_hints": list(
            evidence.get("required_resource_hints", []) or []
        ),
        "alternatives": alternatives,
        "prompt_slots": dict(
            evidence.get("provider_contract", {}).get("prompt_semantic_slots", {})
            or {}
        ),
    }


def render_planner_evidence(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
