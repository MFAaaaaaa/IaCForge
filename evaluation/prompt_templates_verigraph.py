"""Stage-specific prompt templates for planner and compiler calls."""


GRAPH_IR_JSON_SHAPE = r"""
{
  "graph_ir_version": "2.0",
  "resources": [
    {
      "id": "stable_instance_id",
      "type": "aws_resource_type",
      "kind": "resource | data_source | external_input",
      "role": "short requirement-derived role",
      "value_type": "only for external_input"
    }
  ],
  "bindings": [
    {
      "id": "binding:consumer.argument->producer.attribute",
      "consumer": {"resource": "consumer_instance_id", "path": "assignable_argument"},
      "producer": {"resource": "producer_instance_id", "path": "exported_attribute"},
      "kind": "attribute_reference",
      "evidence_id": "optional public evidence id"
    }
  ],
  "constraints": [
    {
      "id": "constraint_1",
      "target": "instance_id.attribute",
      "operator": "equals",
      "value": true,
      "value_kind": "boolean",
      "source_text": "exact visible requirement phrase",
      "confidence": 0.95
    }
  ],
  "explicit_dependencies": [],
  "requirements": [
    {
      "id": "req_1",
      "text": "one atomic visible requirement",
      "implemented_by": ["resource:instance_id", "binding:...", "constraint:..."]
    }
  ],
  "notes": []
}
"""


def baseline_generation_prompt(question_prompt):
    return f"""Here is the actual prompt:
{question_prompt}

Generate one complete Terraform HCL program for the requirement. Return the Terraform in one ```hcl code block.
Do not assume hidden benchmark resource lists, reference outputs, hidden intents, validation errors, plan errors, OPA results, generated HCL, or repair traces.
"""


def _graph_ir_rules():
    return """
Planner rules:
- Return only one valid JSON object, with no Markdown or prose.
- Create a stable instance for each object explicitly required by the visible prompt.
- Distinguish managed `resource`, read-only `data_source`, and `external_input`.
- "Use an existing X" must not silently become a newly managed resource.
- Express value flow as field-level `bindings`: `consumer` is the block and assignable argument receiving the value; `producer` is the block and exported attribute being referenced.
- Never reverse consumer and producer. Example: subnet.vpc_id = vpc.id means consumer=subnet.vpc_id and producer=vpc.id.
- `explicit_dependencies` is only for a genuine Terraform depends_on relation that cannot be represented by a value reference.
- Use structured constraints when a field mapping is known. Keep a semantic requirement with status `unresolved` when it is not known.
- Map every atomic visible requirement to the resources, bindings and constraints that implement it.
- Keep the task graph compact and never invent hidden benchmark expectations.
"""


def resource_graph_ir_prompt_only_prompt(question_prompt):
    return f"""
Construct a typed task Graph IR for the visible Infrastructure-as-Code requirement.

Use only the visible requirement. Do not use benchmark resource lists, reference outputs,
hidden intents, evaluator policies, validation/plan errors, OPA results, generated HCL,
or repair traces.

{_graph_ir_rules()}

Required JSON shape:
{GRAPH_IR_JSON_SHAPE}

Visible requirement:
{question_prompt}
"""


def full_kg_resource_graph_ir_prompt_only_prompt(question_prompt, planner_evidence):
    return f"""
Construct a typed task Graph IR for the visible Infrastructure-as-Code requirement.

Use only the visible requirement and the coarse planner evidence below. Planner evidence
contains resource candidates and possible value-flow relations; it intentionally excludes
full argument lists, computed-attribute inventories, nested-block details, examples and HCL
syntax. A `REQUIRES_VALUE_OF_TYPE` relation does not imply that a new managed resource must
be created: choose a resource, data source, literal or external input from the prompt.

{_graph_ir_rules()}

Candidate rules:
- Prefer high-scoring candidates grounded by exact/lexical/dense matches.
- Candidate resources are recall hints, not an exhaustive list. Derive every object explicitly required by the visible prompt even when it is absent from the candidates.
- Treat schema-name hints as recall aids, never as hard bindings.
- Use provenance-backed dependency candidates when their endpoints are selected.
- A default-resource candidate is valid only when the prompt explicitly requests a default object.

Required JSON shape:
{GRAPH_IR_JSON_SHAPE}

Planner evidence:
{planner_evidence}

Visible requirement:
{question_prompt}
"""


def resource_graph_ir_generation_prompt(question_prompt, graph_ir):
    return f"""
Visible requirement:
{question_prompt}

Normalized typed Graph IR:
{graph_ir}

Generate one complete Terraform HCL program. The Graph IR is binding, not advisory:
1. Generate exactly one block for every resource/data-source instance in the IR.
2. Do not introduce extra managed resources.
3. Realize every field-level binding as a Terraform reference.
4. Use explicit depends_on only for `explicit_dependencies`.
5. Do not assign computed-only attributes.

Return exactly one ```hcl code block.
"""


def resource_graph_ir_schema_generation_prompt(
    question_prompt, graph_ir, schema_contract
):
    return f"""
Visible requirement:
{question_prompt}

Normalized typed Graph IR:
{graph_ir}

IR-guided exact schema contract:
{schema_contract}

Generate one complete Terraform HCL program under these checkable rules:
1. Generate exactly one block for every resource/data-source instance in Graph IR.
2. Do not introduce extra managed resources.
3. Realize every Graph IR binding using a Terraform reference.
4. Assign every required argument and only schema-supported relevant optional arguments.
5. Never assign any computed-only attribute.
6. Use nested block syntax, never object/list assignment in place of a block.
7. Use explicit depends_on only for Graph IR `explicit_dependencies`.
8. Do not emit input variables without defaults.
9. Use each Graph IR instance `id` exactly as its Terraform block label.

Return exactly one ```hcl code block. Do not use hidden evaluator information.
"""


def resource_graph_ir_schema_contract_generation_prompt(
    question_prompt,
    graph_ir,
    schema_contract,
    provider_contract,
    hcl_skeleton="",
):
    skeleton_section = (
        "\nContract-derived deterministic HCL skeleton to complete:\n"
        f"{hcl_skeleton}\n"
        if hcl_skeleton
        else ""
    )
    return f"""
Visible requirement:
{question_prompt}

Normalized typed Graph IR:
{graph_ir}

IR-guided exact schema contract:
{schema_contract}

Canonical task-specific Provider Contract:
{provider_contract}
{skeleton_section}

The Provider Contract is the implementation contract, not an optional note:
1. Generate exactly one block for each `instance_contract`.
   Use the `instance_contract` key exactly as the Terraform block label.
2. Do not introduce managed resources absent from the contract.
3. Realize every `bindings[*].expression` at its specified `consumer_assignment`.
4. Satisfy every `required_assignments` entry; preserve all `must_assign` values.
5. Use visible-prompt values in `should_assign` when present.
6. Never assign `forbidden_assignments`.
7. Use nested block syntax for `nested_blocks`.
8. Use explicit depends_on only for `explicit_dependencies`.
9. If an item is explicitly unresolved, solve it only from the visible prompt and schema;
   do not guess hidden evaluator expectations.
10. Never reference `var.NAME` unless a matching `variable "NAME"` block with a
    concrete default is included in the same program. Prefer visible-prompt literals.
11. Never use an undeclared variable as a shortcut for an
    `unresolved_required_assignments` entry; choose a schema-valid local literal instead.
12. Emit only provider-schema-supported resource types, arguments and nested blocks.

Return one complete Terraform program in exactly one ```hcl code block.
"""


def local_repair_prompt(
    question_prompt, graph_ir, provider_contract, original_hcl, plan_error
):
    return f"""
Repair one Terraform candidate using only the visible prompt, normalized Graph IR,
Provider Contract, original HCL and the deterministic safety or Terraform diagnostic below.
Do not use or infer OPA policy/results. Preserve the same resource instances and bindings.
Never reference an undeclared input variable. Every referenced input variable must
be declared in the same program with a concrete default.
Return one complete repaired program in one ```hcl code block.

Visible prompt:
{question_prompt}

Graph IR:
{graph_ir}

Provider Contract:
{provider_contract}

Original HCL:
{original_hcl}

Terraform diagnostic:
{plan_error}
"""
