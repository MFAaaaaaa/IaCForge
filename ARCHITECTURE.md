# IaCForge Architecture

## Core flow

```text
Prompt
  + Full KG or Paper KG evidence
        |
        v
Planner LLM
        |
        v
resource/dependency Graph IR
        |
        +--> exact provider-schema retrieval
        |
        v
Compiler LLM
        |
        v
normalized Terraform HCL
        |
        +--> validate --> plan --> OPA
        |
        `--> optional one-shot local repair after an initial validate/plan failure
```

KG evidence is always injected at both generation stages. There is no public stage-selection switch.

## Planner

The Planner receives the visible Prompt and evidence retrieved from the selected KG. It emits JSON containing resource instances, dependency edges, and short implementation notes. `evaluation/graph_ir.py` parses and normalizes this JSON before schema retrieval.

## Schema grounding

`evaluation/schema_rag.py` uses Graph IR resource types as exact keys into the bundled AWS provider schema. The resulting context contains required, optional, computed-only, and nested-block facts. Hidden benchmark columns are not used for schema selection.

## Compiler

The Compiler receives four logical inputs:

1. Prompt
2. Graph IR
3. provider Schema context
4. selected KG representation

For Full KG, `evaluation/provider_contract.py` converts retrieved graph evidence and schema facts into the typed resource/dependency Provider Contract used by the retained runs. For Paper KG, the Compiler receives the raw retrieved Paper KG evidence.

The generated program is normalized only by adding Terraform/AWS provider constraints and offline provider settings when missing. No resource blocks or task-specific assignments are synthesized by normalization.

## Validation and evaluation

IaCForge runs `terraform validate`, then creates a plan and converts it to JSON. OPA receives the plan only after generation has finished and the plan succeeds.

When repair is enabled through `VERIGRAPH_MAX_REPAIR_STEPS=1` or a repair mode, one call may run after the initial candidate fails validation or planning. Its inputs are Prompt, normalized Graph IR, provider schema context, current HCL, and the Terraform diagnostic. It receives neither selected-KG data nor OPA policy/results.

## Knowledge graphs

Full KG covers the complete bundled AWS provider resource and data-source universe. Its edges come from official documentation examples, provider schema relationships, and conservative attribute-to-resource reference rules.

Paper KG uses bundled document, argument/block, example, and reference-traversal assets. Its benchmark-scoped relation edges are reported separately from the dataset-independent Full KG method.
