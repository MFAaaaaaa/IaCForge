# Paper KG Provenance

## Source and interpretation

`source/` comes from the paper replication package and contains parsed Terraform documentation, resource JSON, KG JSON, examples, and reference/dependency relations. `PAPER_REPLICATION_LICENSE.txt` records the source license.

The graph contains benchmark-scoped relation edges. It is therefore a leakage-analysis condition, not a leakage-free knowledge source.

## Retrieval

Paper KG retrieval follows the retained experiment path:

1. retrieve the top five Terraform document chunks for the visible Prompt;
2. identify direct resources from those chunks;
3. retrieve relevant optional arguments and blocks per resource;
4. traverse `REFERENCES` relations from required elements and selected optional elements;
5. retrieve one resource example when enabled;
6. serialize the resulting graph context for both Planner and Compiler.

If a bundled Chroma collection cannot be opened, direct-resource retrieval falls back to BM25 over the same Paper KG records.

The embedding model is `sentence-transformers/all-mpnet-base-v2`. Bundled collection counts are:

| Collection | Count |
|---|---:|
| `terraform_resources` | 5,996 |
| `terraform_doc_chunks` | 1,390 |
| `terraform_examples` | 422 |
| `terraform_arguments_blocks` | 4,419 |
| Total | 12,227 |
