# IaCForge

IaCForge generates Terraform HCL from a natural-language infrastructure requirement through a graph-first, schema-grounded pipeline:

```text
Prompt + selected KG
  -> Planner
  -> resource/dependency Graph IR
  -> exact AWS provider schema grounding
  -> Compiler
  -> Terraform HCL
  -> terraform validate
  -> terraform plan
  -> OPA evaluation
```

The selected knowledge graph is one of:

- **Full KG**: all AWS provider resource/data-source nodes from the bundled provider schema and official documentation, with official-example and schema-derived relation edges. The Planner receives retrieved Full KG evidence; the Compiler receives a typed Provider Contract derived from the same evidence.
- **Paper KG**: bundled graph assets and Chroma indexes with document, argument/block, example, and reference traversal. It contains benchmark-scoped relation edges and is reported separately from Full KG. Both Planner and Compiler receive retrieved Paper KG evidence.

An optional one-shot local repair runs only when the initial candidate fails `terraform validate` or `terraform plan`. It is enabled by `VERIGRAPH_MAX_REPAIR_STEPS=1` or by the repair modes. The repair call receives the Prompt, Graph IR, provider schema context, current HCL, and the Terraform diagnostic. It does not receive KG evidence, Provider Contract data, or OPA feedback.

## Inputs by generation stage

The Planner has two logical inputs: `Prompt` and the selected `KG evidence`.

The Compiler has four logical inputs: `Prompt`, `Graph IR`, `Schema`, and the selected KG representation. The fourth input is a typed Provider Contract for Full KG and raw retrieved evidence for Paper KG.

## Run

Install the core dependencies, prepare an OpenAI-compatible Qwen endpoint, and choose one of the four supported modes:

```bash
pip install -r requirements.txt

MODE=full_kg MODEL=qwen2.5-coder-3b scripts/run_framework.sh
MODE=full_kg_repair MODEL=qwen2.5-coder-14b scripts/run_framework.sh
MODE=paper_kg MODEL=qwen2.5-coder-3b scripts/run_framework.sh
MODE=paper_kg_repair MODEL=qwen2.5-coder-14b scripts/run_framework.sh
```

Paper KG retrieval additionally requires the packages in `requirements-paper-kg.txt`.

The benchmark has 458 rows. During generation, IaCForge reads only the `Prompt` column and the selected method assets. `Rego intent` is accessed only after HCL generation and a successful Terraform plan.

## Repository layout

- `evaluation/`: Planner, Graph IR, schema grounding, KG retrieval, Compiler, Terraform evaluation, OPA, and optional repair.
- `data/full_kg/`: Full KG build script and bundled graph.
- `data/paper_kg/`: Paper KG source, Chroma indexes, license, and provenance.
- `configs/models/`: the model configurations used by the retained runs.
- `results/`: retained result/log pairs for the reported method runs.
- `scripts/run_framework.sh`: current method entry point.
- `scripts/verify_package.py`: package integrity and boundary checks.

See `ARCHITECTURE.md`, `LEAKAGE_POLICY.md`, and `REPRODUCIBILITY.md` for the method contract and verification commands.
