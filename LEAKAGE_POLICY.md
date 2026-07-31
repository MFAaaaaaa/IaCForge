# Data Leakage Policy

## Allowed generation-time inputs

- visible IaC-Eval `Prompt`;
- Graph IR generated from that prompt;
- public Terraform AWS provider schema;
- public Terraform AWS provider documentation and examples;
- KG nodes/edges constructed from those public sources;
- deterministic retrieval output keyed by prompt SHA-256.

## Forbidden generation-time inputs

- IaC-Eval `Resource`;
- IaC-Eval `Intent`;
- IaC-Eval `Rego intent`;
- IaC-Eval `Reference output`;
- reference Terraform/HCL;
- validation or plan errors;
- OPA outputs or policy predicates;
- previously generated HCL;
- repair traces or evaluator feedback.

## Evaluation-only inputs

`Rego intent` may be read only after HCL generation and successful Terraform
plan. It must never be passed to Graph IR, Schema RAG, KG retrieval, or HCL
generation.

## Cache policy

The offline KG cache may use only `Prompt` and public KG data. Cache entries
are keyed by SHA-256 of prompt text. A cache miss is a hard error by default;
online fallback requires explicit `IAC_ALLOW_ONLINE_KG_RETRIEVAL=1`.

## Manual retrieval rules

The current provider-contract retriever contains hand-written resource aliases,
concept bundles, and reference-attribute hints. They must:

- express public AWS/Terraform semantics only;
- never encode row IDs, reference outputs, hidden intents, or OPA predicates;
- be documented as heuristic retrieval knowledge;
- be included in an ablation if the paper makes a fully automatic KG claim.

## Experimental protocol

Do not tune retrieval rules, prompts, or compiler behavior on the held-out test
outputs. Use a declared development split and record its row IDs. Final results
must record model weights, quantization, serving engine, decoding parameters,
Terraform version, AWS provider version, schema/KG hashes, and row selection.
