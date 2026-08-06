# Knowledge Graph Retrieval

IaCForge has exactly two KG retrievers.

## Full KG

`provider_contract_retriever.py` loads `data/full_kg/provider_kg`. It matches the visible Prompt against deterministic resource aliases and schema/document phrases, adds matched cloud-concept bundles, ranks the matches deterministically, and performs bounded dependency closure through official/schema relation edges. The returned evidence supplies Graph IR candidates and dependency hints, then feeds the typed Provider Contract used by the Compiler.

## Paper KG

`paper_replication_json_retriever.py` loads `data/paper_kg/source` and `data/paper_kg/chroma`. It performs document-chunk retrieval, argument/block retrieval, example retrieval, and `REFERENCES` traversal. The returned raw graph context enters both Planner and Compiler.

Paper KG contains benchmark-scoped relation edges and is reported separately from Full KG. Full KG is the dataset-independent method KG.

`build_paper_replication_chroma.py` rebuilds the four Paper KG collections from the bundled source.
