# Architecture

## Stage Contracts

```text
Prompt
  -> KG profile selector
  -> optional PlannerEvidence projection
  -> Graph IR generation and safe parsing
  -> IR/schema consistency check
  -> exact SchemaProjection
  -> optional instance-level ProviderContract
  -> HCL generation and normalization
  -> validate -> plan
  -> optional local repair after plan failure
  -> final validate/plan -> OPA
```

The evaluator uses four primary typed boundaries:

1. `Prompt + optional PlannerEvidence -> GraphIR`
2. `GraphIR -> SchemaProjection`
3. `GraphIR + SchemaProjection + optional KG -> ProviderContract`
4. `Prompt + GraphIR + SchemaProjection + optional ProviderContract -> HCL`

`IAC_KG_INJECTION_STAGE=ir` enables only planner evidence. `hcl` enables only
the KG-derived Provider Contract. `both` enables both interfaces. Schema
grounding is separate from KG selection.

## KG Adapters

`evaluation/eval_verigraph.py` selects one evidence adapter:

- `clean_multigranular`: version-aligned public provider KG and offline cache.
- `paper`: paper replication JSON, Chroma, lexical fallback, reranking, and
  reference expansion.
- `hybrid_cached_evidence_rebuilt_kg`: immutable first-build evidence used for
  historical comparability, with the second reconstructed KG kept alongside
  it for provenance and future rebuilding.

The paper adapter defaults to top-2 resources and can expand dependencies from
`reference_relations`. Optional arguments/blocks are retrieved from the
`terraform_arguments_blocks` Chroma collection. The clean profile uses stable
typed nodes/edges, exact aliases, BM25, optional dense retrieval, and graph
expansion.

## Graph IR and Schema

Graph IR v2 represents resource, data-source, and external-input instances;
field-level bindings; structured constraints; explicit dependencies; and
unresolved requirements. Invalid model text becomes a canonical empty IR and
is marked as an IR generation failure.

`IRSchemaChecker` checks provider type/kind, assignable source fields,
exportable target attributes, approximate type compatibility, and binding
endpoints. It applies only unique high-confidence field-name corrections.
`schema_rag.py` then creates the task-relevant schema context.

## Provider Contract

When HCL-stage KG injection is enabled, `provider_contract.py` compiles the
normalized IR, schema projection, and KG evidence into an instance-level
contract. It contains required/allowed assignments, references, relevant
nested blocks, explicit dependencies, forbidden computed assignments, and
unresolved constraints. It may also produce a deterministic HCL skeleton.

The broad raw candidate KG is not passed directly to the HCL model. The model
receives the projected Provider Contract.

## Local Repair

`evaluation/local_repair.py` implements a strict policy:

- trigger only after the first Terraform plan fails;
- at most one repair model call;
- inputs: visible Prompt, normalized Graph IR, provider schema context,
  current HCL, and Terraform plan diagnostic;
- no raw KG evidence;
- no KG-derived Provider Contract;
- no OPA policy or result.

The repaired candidate is normalized, validated, and planned again. Validation
failure before the first plan does not trigger repair. Graph IR and HCL may
carry indirect upstream KG influence, so this is a no-direct-KG repair
boundary, not a claim that all causal KG influence has been removed.

## Evaluation and Logging

All modes share the same HCL extraction and normalization path. Logs preserve
raw/normalized IR and HCL, normalization diffs, request parameters, token use,
latencies, KG profile and injection stage, retrieval provenance, and repair
policy. OPA is evaluation-only and runs after generation and planning.

