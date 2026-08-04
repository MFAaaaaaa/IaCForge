# Paper KG Provenance

## Source

The raw JSON under `source/notebooks_kg_construction/` comes from the paper
replication package and includes parsed Terraform documentation, paper KG JSON,
Terraform documentation JSON with LLM summaries, and resource reference/
dependency relations. `PAPER_REPLICATION_LICENSE.txt` records its license.

The paper resource universe may be benchmark-scoped. Runtime use is disabled
unless `IAC_ALLOW_BENCHMARK_SCOPED_PAPER_KG=1` is explicitly set.

## Retrieval Construction

1. Search Chroma document/resource collections using the visible Prompt.
2. Use in-code BM25 and resource-label matching as lexical fallback and for
   hybrid reranking.
3. Keep a conservative resource top-k; default
   `IAC_KG_PAPER_TOP_K_RESOURCES=2`, with bounded candidate expansion.
4. Expand directly matched resources through `reference_relations`, matching
   the role of GR-Ref dependency traversal.
5. Search `terraform_arguments_blocks` for relevant optional arguments and
   nested blocks.
6. Emit the bounded KG evidence JSON consumed by Graph IR and/or HCL stages.

The Chroma embedding model is
`sentence-transformers/all-mpnet-base-v2`. Bundled collection counts are:

| Collection | Count |
| --- | ---: |
| `terraform_resources` | 5,996 |
| `terraform_doc_chunks` | 1,390 |
| `terraform_examples` | 422 |
| `terraform_arguments_blocks` | 4,419 |
| Total | 12,227 |

## Leakage Label

Use this data only for experiments labelled `paperkg` or paper KG injection
stage selection. Do not combine it with clean multigranular KG results under a
single no-leakage label.

