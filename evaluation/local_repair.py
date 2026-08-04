"""One-shot Terraform plan-error repair with an explicit no-KG boundary."""

from __future__ import annotations

import prompt_templates_verigraph as prompt_templates


REPAIR_TRIGGER = "terraform_plan_failed"
MAX_REPAIR_CALLS = 1


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
        "max_calls": MAX_REPAIR_CALLS,
        "inputs": [
            "Prompt",
            "Graph IR",
            "provider schema context",
            "current HCL",
            "Terraform plan diagnostic",
        ],
        "raw_kg_in_repair": False,
        "provider_contract_in_repair": False,
        "opa_feedback_used": False,
    }
