"""Stage-specific prompt templates for planner and compiler calls."""


GRAPH_IR_JSON_SHAPE = r"""
{
  "resources": [
    {
      "type": "aws_resource_type",
      "name": "logical_name",
      "depends_on": ["aws_other_type.other_name"],
      "reason": "short prompt-derived reason for this resource"
    }
  ],
  "dependencies": [
    {
      "from": "aws_resource_type.logical_name",
      "to": "aws_other_type.other_name",
      "reason": "short prompt-derived reason for this dependency"
    }
  ],
  "notes": ["short prompt-derived implementation notes"]
}
"""


def resource_graph_ir_kg_prompt(question_prompt, kg_evidence):
    return f"""
Decompose the following Infrastructure-as-Code requirement into a Terraform resource dependency graph before writing Terraform.

Use only:
1. the visible requirement text below;
2. the selected Terraform/cloud KG evidence pack below.

Do not assume access to benchmark resource lists, reference outputs, hidden intents, evaluator policies, validation errors, plan errors, OPA results, generated HCL, or repair traces.

Resource selection rules:
- Add every resource implied by the visible requirement.
- Add high-confidence `required_resource_hints` when the matched cloud feature cannot be represented correctly without that Terraform resource type.
- Prefer `candidate_resources` resource types. If you use a type outside them, it must be explicitly named by the visible requirement and valid in the AWS provider schema.
- Keep the graph compact.

Dependency and schema rules:
- Prefer `dependency_hints` for Terraform references.
- Put KG nested-block, conflict, and anti-pattern guidance in `notes`.
- Do not assign computed-only attributes.

Return only valid JSON. Do not include Markdown fences or explanatory prose.
Use stable Terraform-safe names with underscores only.

JSON shape:
{GRAPH_IR_JSON_SHAPE}

Selected KG evidence pack:
{kg_evidence}

Requirement:
{question_prompt}
"""


def resource_graph_ir_schema_contract_generation_prompt(
    question_prompt,
    graph_ir,
    schema_contract,
    provider_contract,
):
    return f"""
Visible requirement:
{question_prompt}

Planner-generated Graph IR text:
{graph_ir}

Provider schema context:
{schema_contract}

Typed Provider Contract:
{provider_contract}

The Provider Contract is the implementation contract, not an optional note:
1. Generate every resource whose `generation_policy` is `must_generate_resource`.
2. Generate optional contract resources only when required by the visible Prompt or dependency closure.
3. Satisfy `required_attributes` and never assign `forbidden_computed_attributes`.
4. Preserve visible values in `prompt_semantic_slots` and apply matching `value_bindings`.
5. Use `dependency_contracts` when both endpoint types are generated, replacing placeholder labels with actual labels.
6. Preserve the exact endpoint resource types in every dependency contract.
7. Emit required `nested_block_contracts` with block syntax; emit optional blocks only when the Prompt or a dependency requires them.
8. Treat `usage_constraints` and `negative_constraints` as provider-syntax guardrails.
9. Do not introduce input variables without realistic defaults.

Return one complete Terraform program in exactly one ```hcl code block.
"""


def resource_graph_ir_schema_kg_generation_prompt(
    question_prompt, graph_ir, schema_context, kg_evidence
):
    """HCL prompt used when raw KG evidence is supplied to the Compiler."""

    return f"""
Here is the actual prompt:
{question_prompt}

Terraform resource dependency graph inferred only from the prompt:
{graph_ir}

Terraform AWS provider schema summary retrieved only for resource types inferred from the graph IR:
{schema_context}

Selected Terraform/cloud KG evidence retrieved only from the visible prompt:
{kg_evidence}

Generate one complete Terraform HCL program. Use only valid Terraform AWS provider resources and attributes. Instantiate the graph resources and preserve dependency edges through Terraform references.
Return the Terraform in one ```hcl code block.
Hard requirements:
- Cover the user-visible requirement in the prompt.
- Use the schema summary and KG evidence only as syntax/dependency guidance; do not assume hidden benchmark resource lists, reference outputs, hidden intents, evaluator policies, validation errors, plan errors, OPA results, generated HCL, or repair traces beyond information already encoded in the selected KG.
- Prefer resource types and dependency references supported by the graph IR. Use KG candidate resources only when required by the visible prompt or a necessary dependency.
- Use KG dependency hints for reference expressions when they match generated resources.
- Use KG nested-block hints and provider schema nested blocks with block syntax, not list/object assignment syntax.
- Do not emit input variables without defaults.
- Do not assign computed-only attributes.
"""


def local_repair_prompt(
    question_prompt, graph_ir, schema_context, original_hcl, plan_error
):
    return f"""
Repair one Terraform candidate using only the visible prompt, normalized Graph IR,
provider schema context, original HCL and Terraform validate/plan diagnostic below.
Do not use or infer OPA policy/results. Preserve the same resource instances and bindings.
No KG evidence or KG-derived Provider Contract is supplied in this repair call.
Return one complete repaired program in one ```hcl code block.

Visible prompt:
{question_prompt}

Graph IR:
{graph_ir}

Provider schema context:
{schema_context}

Original HCL:
{original_hcl}

Terraform diagnostic:
{plan_error}
"""
