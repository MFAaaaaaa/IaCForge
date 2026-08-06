# Data

## Benchmark

`complete/data.csv` contains 458 IaC-Eval rows. Generation reads only `Prompt`. Hidden evaluation fields remain in the CSV because they are needed after generation for scoring.

## Provider schema

`schema_grounding/aws-provider-schema.json` is the exact AWS provider schema used for Graph IR grounding and Provider Contract construction.

## Full KG

`full_kg/provider_kg/` contains:

- `resources.jsonl`: all provider resource/data-source records;
- `kg_edges.jsonl`: official-example and schema-derived relation edges;
- `docs/`: bundled official provider documentation;
- `metadata.json`: construction counts and leakage policy.

`full_kg/build_full_kg.py` rebuilds this graph from an AWS provider source tree and a `terraform providers schema -json` export. It does not accept the benchmark dataset.

## Paper KG

`paper_kg/source/` contains the reproduced paper package used by the Paper KG retriever. `paper_kg/chroma/` contains its four retrieval collections. The graph includes benchmark-scoped relation edges and is retained only for the Paper KG leakage-analysis condition.

`paper_kg/PAPER_REPLICATION_LICENSE.txt` records the source license and `paper_kg/PROVENANCE.md` records construction and index counts.
