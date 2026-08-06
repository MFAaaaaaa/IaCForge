"""One-shot not-plannable repair with an explicit no-KG boundary."""

from __future__ import annotations

import os

import prompt_templates_verigraph as prompt_templates


REPAIR_TRIGGER = "initial_candidate_not_plannable"
MAX_REPAIR_CALLS = 1
ENV_MAX_REPAIR_STEPS = "VERIGRAPH_MAX_REPAIR_STEPS"


def configured_max_steps(cli_enabled: bool = False) -> int:
    """Resolve the original repair switch while preserving one-shot repair."""
    raw = os.environ.get(ENV_MAX_REPAIR_STEPS, "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            requested = 0
    else:
        requested = MAX_REPAIR_CALLS if cli_enabled else 0
    if cli_enabled:
        requested = max(requested, MAX_REPAIR_CALLS)
    return max(0, min(requested, MAX_REPAIR_CALLS))


def enabled(cli_enabled: bool = False) -> bool:
    return configured_max_steps(cli_enabled) > 0


def build_prompt(question_prompt, graph_ir, schema_context, current_hcl, diagnostic):
    """Build the repair prompt without raw KG evidence or provider contracts."""
    return prompt_templates.local_repair_prompt(
        question_prompt,
        graph_ir,
        schema_context,
        current_hcl,
        diagnostic,
    )


def policy_manifest():
    return {
        "trigger": REPAIR_TRIGGER,
        "includes_validate_failure": True,
        "max_calls": MAX_REPAIR_CALLS,
        "switch": ENV_MAX_REPAIR_STEPS,
        "inputs": [
            "Prompt",
            "Graph IR",
            "provider schema context",
            "current HCL",
            "Terraform validate/plan diagnostic",
        ],
        "raw_kg_in_repair": False,
        "provider_contract_in_repair": False,
        "opa_feedback_used": False,
    }
