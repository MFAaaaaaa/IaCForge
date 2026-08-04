# Data

## Evaluation and Schema

- `complete/data.csv`: 458 IaC-Eval rows and 451 unique Prompt values.
  Generation reads only `Prompt`; hidden intent/resource and Rego are reserved
  for post-generation metrics.
- `schema_grounding/aws-provider-schema.json`: AWS Provider 5.90.0 schema.
- `provider_mirror/`: optional offline provider mirror.

## Clean Multigranular KG

`leakfree_multigranular_kg/` contains public Terraform AWS 5.90.0 docs/schema,
resource records, examples, reference edges, stable typed nodes/edges, and the
451-prompt offline evidence cache used by the 458-row dataset. Duplicate
prompts share a cache entry by SHA-256.

This is the main clean profile. Rebuild instructions are in
`leakfree_multigranular_kg/BUILD.md`.

## Paper KG

`paper_kg/source/notebooks_kg_construction/` contains the paper replication
package's raw inputs:

- `terraform_json_docs_with_summaries/`;
- `kg_json/`;
- `reference_relations/`.

`paper_kg/chroma/` contains the reconstructed Chroma vector store using
`sentence-transformers/all-mpnet-base-v2`. It has 5,996 resource/entity docs,
1,390 document chunks, 422 examples, and 4,419 argument/block docs. See
`paper_kg/PROVENANCE.md` and the bundled replication license.

This profile may be benchmark-scoped and requires explicit opt-in. It is for
paper-faithful comparisons, not the main no-leakage claim.

## Half-Paper/Half-Full Historical KG

`hybrid_paper_fullkg/evidence_v1/publickg_full458.jsonl` stores evidence from
the first KG construction. It contains one record per unique Prompt (451),
covering all 458 dataset rows through Prompt SHA-256 keys.

The original KG data used for that evidence was later lost.
`hybrid_paper_fullkg/kg_v2_rebuilt/` is the second construction from public
Terraform AWS 5.90.0 docs/schema. Its content may differ slightly from the
first KG, but aggregate results are expected to remain close. See
`hybrid_paper_fullkg/PROVENANCE.md`.

## Rebuild Order

```text
schema/docs
  -> KG records and reference edges
  -> optional typed graph / Chroma index
  -> prompt evidence cache
  -> experiments
```

Never rebuild paper/hybrid data using IaC-Eval hidden columns, reference HCL,
OPA predicates, or evaluation diagnostics.

