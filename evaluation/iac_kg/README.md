# Leakage-Free Provider Contract KG

This module retrieves Terraform AWS provider contracts using only:

- visible task `Prompt` text;
- public Terraform AWS provider documentation and examples;
- the public provider schema;
- the KG built from those public sources.

It must not read IaC-Eval `Resource`, `Intent`, `Rego intent`,
`Reference output`, generated HCL, validation/plan/OPA output, or repair traces.

## Components

- `provider_contract_retriever.py`: exact/alias, BM25, optional dense and
  graph-aware resource linking plus public evidence retrieval.
- `typed_kg.py`: stable entity/edge IDs, provenance/confidence normalization,
  quality reporting and audit sampling.
- `audit_metrics.py`: precision and Cohen's kappa over double annotations.
- `offline_provider_contract_cache.py`: canonical prompt-SHA-256 cache with
  complete retrieval provenance.
- `../../data/leakfree_multigranular_kg/build_leakfree_multigranular_kg.py`:
  builds full-provider KG nodes and reference edges.
- `../../scripts/build_offline_cache.py`: materializes hybrid-v2 retrieval for
  the 458 visible prompts.

## Multi-granular representation

Full evidence contains resource candidates, schema facts, nested blocks,
dependency templates, safe literal hints and public-example patterns. The
planner receives only `PlannerEvidence`; the compiler receives the independent
instance-level canonical contract compiled by `evaluation/provider_contract.py`.
Runtime entity linking also uses audited
hand-written AWS aliases and concept bundles in
`provider_contract_retriever.py`; these are retrieval heuristics, not hidden
benchmark labels. They must be disclosed and ablated when making claims about
fully automatic retrieval.

`MODE=full` refuses to silently fall back to online retrieval when a cache
entry is missing. Set `IAC_ALLOW_ONLINE_KG_RETRIEVAL=1` only for an explicitly
recorded online-retrieval experiment.
