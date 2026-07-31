# Architecture

## Offline layer

The offline layer is version aligned:

```text
AWS Provider Schema 5.90.0
+ AWS Provider Docs 5.90.0
+ Official HCL examples
  -> stable typed Provider KG
  -> resource semantic index
  -> dependency evidence index
  -> exact schema index
  -> provenance-complete prompt cache
```

Typed IDs use forms such as:

```text
aws@5.90.0::resource::aws_subnet
aws@5.90.0::resource::aws_subnet::argument::vpc_id
aws@5.90.0::resource::aws_vpc::attribute::id
```

Reference edges record source/target paths, provenance, supporting document,
support count, confidence and provider version. Most edges mean
`REQUIRES_VALUE_OF_TYPE`; they do not imply managed-resource creation.

## Online layer

The online pipeline has four typed boundaries:

1. `Prompt -> PlannerEvidence`
2. `PlannerEvidence -> GraphIR v2`
3. `GraphIR -> SchemaProjection`
4. `GraphIR + SchemaProjection + KG -> ProviderContract v2`

The planner never receives full optional/computed fields, nested-block
inventories or HCL examples. The compiler receives only Graph IR instances and
their exact task-specific contract, not the original broad candidate set.

## Graph IR v2

Graph IR v2 contains:

- instance nodes with `resource`, `data_source` or `external_input` kind;
- field-level `bindings`;
- structured constraints and unresolved semantic constraints;
- true `explicit_dependencies`;
- requirement-to-implementation coverage.

Legacy v1 IR is readable and normalized to v2. Invalid model text becomes an
empty canonical v2 object and is marked `ir_generation_failure`.

## Schema consistency

`IRSchemaChecker` checks:

- provider type and node kind;
- source argument existence/assignability;
- target attribute existence/exportability;
- approximate provider-schema type compatibility;
- declared binding endpoints.

Only a unique, high-confidence field-name correction is applied. Structural
resource changes are never performed automatically.

## Provider Contract

The canonical contract is instance-level. It contains:

- required and allowed assignments;
- `must_assign` reference expressions;
- visible-prompt and structured-constraint `should_assign` values;
- computed-only forbidden assignments;
- relevant nested blocks;
- explicit dependencies;
- unresolved requirements and constraints.

The HCL prompt, log record and contract validator all consume these exact field
names.

## Evaluation

All modes share exactly the same HCL extraction and normalization function.
The log distinguishes raw model HCL from normalized HCL and stores a unified
diff. Before Terraform, structural metrics report IR node/binding realization,
extra/missing resources and schema contract violations.

OPA remains post-generation evaluation. It never influences strict generation.
`full_repair1` may use one Terraform validation/plan diagnostic, but never OPA
feedback.
