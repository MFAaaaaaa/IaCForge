# Data

- `complete/data.csv`: IaC-Eval evaluation data. Generation reads only
  `Prompt`; gold `Resource` and `Rego intent` are post-generation metrics.
- `schema_grounding/aws-provider-schema.json`: AWS Provider 5.90.0 schema.
- `leakfree_multigranular_kg/terraform_aws_5.90.0_public_kg/`: matching public
  provider documentation, records, examples and reference edges.
- `typed_nodes.jsonl` / `typed_edges.jsonl`: stable formal KG materialization
  produced by `scripts/build_typed_kg.py`.
- `resource_dense_index.json`: optional resource-level semantic index. It
  contains only names, aliases, descriptions and short purpose text—not all
  arguments/blocks.
- `offline_retrieval/provider_contract_full458.jsonl`: reproducibility cache
  keyed by prompt SHA-256 with full retrieval provenance.
- `provider_mirror/`: optional local mirror containing AWS Provider 5.90.0.

Rebuild order:

```text
schema/docs -> typed KG -> optional dense index -> offline cache -> experiments
```

The cache is not a source of benchmark knowledge. Non-IaC-Eval prompts use the
same online retriever through `evaluation/iacforge_cli.py retrieve`.
