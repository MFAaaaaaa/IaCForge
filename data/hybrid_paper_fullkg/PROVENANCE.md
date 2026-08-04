# Hybrid KG Provenance

This directory preserves a historical half-paper/half-full configuration.

- `evidence_v1/publickg_full458.jsonl` is the evidence generated during the
  first construction. It has 451 Prompt-keyed records, covering all 458 rows
  because seven rows repeat an existing Prompt.
- The original raw KG used to create that evidence was subsequently lost.
- `kg_v2_rebuilt/` is the second KG construction from public Terraform AWS
  Provider 5.90.0 documentation and schema.

The first-build evidence and second-build KG are not claimed to be byte- or
content-identical. Some resource text or edges may differ slightly, but the
expected aggregate results should remain close. Results using this directory
must be labelled `hybrid_cached_evidence_rebuilt_kg` or
`half_paper_half_fullkg`, never as the clean multigranular KG.

The intended construction uses Prompt retrieval over Chroma documentation
blocks, BM25/resource-label lexical fallback, hybrid reranking, conservative
top-k, reference-relation dependency expansion, and optional argument/block
retrieval. No hidden IaC-Eval intent, Rego, reference HCL, OPA output, or
Terraform diagnostic is a KG construction input.

