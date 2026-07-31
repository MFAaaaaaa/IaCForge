# Cleanup Report — 2026-07-30

## Archive boundary

The historical experiment workspace is retained without code cleanup:

```text
/home/fameng/zzzhong/iac-eval/evaluation
```

It remains the source of exploratory evaluators, 85 run scripts, 166 logs,
208 result CSVs, worklogs, quarantine data, and intermediate experiments.

The pre-cleanup paper package snapshot is:

```text
/home/fameng/zzzhong/LeakFreeKG-GraphIR.before-cleanup-20260730.tar.gz
SHA256 f67dac1a967d2b9e5f4ee795654c6338a9b97fc81b7afd043b074901855877eb
```

The cleaned package was subsequently renamed to:

```text
/home/fameng/zzzhong/IaCForge
```

## Completed cleanup

- separated Graph IR parsing/validation into `evaluation/graph_ir.py`;
- separated deterministic Schema RAG into `evaluation/schema_rag.py`;
- documented KG construction, retrieval heuristics, and leakage boundary;
- added KG cache coverage validation;
- restored IaC-Eval-compatible arbitrary-package and Rego-v1 OPA evaluation;
- corrected archived OPA metrics;
- removed the hard-coded Terraform CLI configuration;
- copied the historical AWS Provider 5.100.0 mirror into the package;
- added atomic per-row checkpointing, resume, and original row-ID selection;
- protected archived results by writing reruns under a timestamped directory;
- added model API retry handling and generic `generate_text`;
- added integrity/result scripts and 10 unit/integration tests;
- documented architecture, leakage policy, provider-version provenance, and
  reproducibility.

No model generation experiment was launched during cleanup.
