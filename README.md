# IaCForge

IaCForge implements stage-specific task–provider grounding for Terraform AWS
generation:

```text
Visible Prompt
  -> Hybrid resource linking
  -> Planner Evidence projection
  -> LLM -> Typed task Graph IR v2
  -> safe parse + provider-schema consistency checking
     -> invalid bindings only: drop those bindings and re-check the IR
     -> invalid IR: safe Prompt-only compiler fallback
  -> IR-guided exact Schema Grounding
  -> instance-level Provider Contract validation
     -> invalid Contract: Graph IR + Schema compiler fallback
  -> LLM -> one HCL candidate
  -> deterministic input-variable safety check + optional prompt-only repair
  -> common Normalize
  -> validate -> plan -> OPA
```

The historical research workspace remains archived, unchanged, at:

```text
/home/fameng/zzzhong/iac-eval/evaluation
```

## What changed in v2

- Terraform, schema, KG and provider execution are aligned to AWS Provider
  `5.90.0`.
- Full KG evidence is projected into separate planner and compiler interfaces.
- Graph IR v2 distinguishes resources, data sources and external inputs, and
  represents field-level bindings and structured constraints.
- Malformed or empty IR is never forwarded to the compiler as a hard
  constraint. Schema-invalid bindings can be removed deterministically while
  preserving valid provider nodes; the reduced IR is checked again.
- Exact Schema Grounding returns a structured task-relevant projection rather
  than all optional fields.
- `Graph IR + Schema + KG` is compiled into one canonical, instance-level
  Provider Contract with exactly matching prompt fields.
- Planner and HCL calls use separate deterministic decoding settings.
- Raw/normalized IR, raw/normalized HCL, normalization diffs, timings, token
  counts and mechanism metrics are logged.
- Optional `full_repair1` repair receives only Terraform diagnostics—never OPA
  policies or results.

## Modules

- `evaluation/graph_ir.py`: Graph IR v2 parsing, v1 compatibility, validation
  and safe fallback.
- `evaluation/ir_schema_checker.py`: exact kind/field/type consistency checking,
  high-confidence path correction, and schema-invalid binding salvage.
- `evaluation/evidence_projection.py`: planner-only KG projection.
- `evaluation/schema_rag.py`: IR-guided exact schema grounding.
- `evaluation/provider_schema.py`: provider schema access and structured schema
  projections.
- `evaluation/provider_contract.py`: canonical task-specific Provider Contract
  compiler and HCL skeleton.
- `evaluation/iac_kg/provider_contract_retriever.py`: exact, BM25, optional
  dense and graph-aware hybrid retrieval.
- `evaluation/iac_kg/typed_kg.py`: stable KG entity/edge identities,
  provenance, confidence and quality reporting.
- `evaluation/iac_kg/offline_provider_contract_cache.py`: canonical,
  provenance-complete offline cache.
- `evaluation/hcl_metrics.py`: pre-Terraform IR/HCL mechanism metrics.
- `evaluation/hcl_safety.py`: deterministic undeclared/defaultless input-variable
  detection before Terraform validation.
- `evaluation/eval_verigraph.py`: generation, normalization, evaluation,
  checkpoint and repair orchestration.

See [details.md](details.md) for every module’s concrete inputs and outputs.

## Modes

| Mode | Planner KG | Schema | Compiler KG | Repair |
| --- | ---: | ---: | ---: | ---: |
| `baseline` | no | no | no | no |
| `ir_only` | no | no | no | no |
| `ir_schema` | no | yes | no | no |
| `planner_kg` | yes | yes | no | no |
| `compiler_kg` | no | yes | yes | no |
| `full` / `full_strict` | yes | yes | yes | no |
| `full_repair1` | yes | yes | yes | at most one |

Retrieval ablations are selected with:

```bash
IAC_RETRIEVAL_MODE=lexical       # exact/alias + BM25
IAC_RETRIEVAL_MODE=dense         # exact/alias + configured dense index
IAC_RETRIEVAL_MODE=hybrid
IAC_RETRIEVAL_MODE=hybrid_graph  # default
```

Dense retrieval is optional and activates only when
`resource_dense_index.json` has been built and `IAC_DENSE_RETRIEVAL=1`.

## Build version-aligned artifacts

```bash
python3 scripts/build_typed_kg.py
python3 evaluation/iacforge_cli.py kg-quality \
  --audit-sample results/reference_edge_audit.csv

# Requires an OpenAI-compatible embeddings endpoint.
python3 scripts/build_dense_index.py --model /path/or/served-embedding-model

python3 scripts/build_offline_cache.py
python3 scripts/evaluate_retrieval.py
```

The offline cache is an execution optimization, not benchmark-specific
knowledge. A non-IaC-Eval prompt can use the same retriever:

```bash
cd evaluation
python3 iacforge_cli.py retrieve \
  --prompt "Create a VPC and a public subnet" \
  --projection planner
```

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v

MODE=full \
MODEL=qwen2.5-coder-3b \
MAX_ROWS=458 \
./scripts/run_framework.sh
```

Defaults:

```text
Terraform                 1.9.8
AWS Provider              5.90.0
Graph IR                  2.0
Provider Contract         2.0
retriever                 hybrid-v2
IR decoding               temperature=0, top_p=1, guided JSON
HCL decoding              temperature=0, top_p=1
HCL skeleton              disabled (explicit ablation only)
Planner candidate cap     8 (`IAC_PLANNER_MAX_CANDIDATES`)
Variable safety repair    enabled (`IAC_REPAIR_UNDECLARED_VARIABLES=1`)
```

Invalid Graph IR is never forwarded as an empty hard constraint. An IR with no
AWS resource/data-source nodes falls back to visible-Prompt generation. If only
bindings fail provider-schema checking, those bindings are dropped and the
reduced IR is rechecked before Schema Grounding. A schema-valid IR with an
invalid Provider Contract falls back to Prompt + Graph IR + exact Schema.
Rejected artifacts remain in provenance but are not shown to the Compiler as
binding implementation contracts.

Changing the provider version without rebuilding the schema, documentation KG,
dense index and offline cache fails closed.

## Leakage boundary

Before generation, the pipeline may read only the visible `Prompt` and public
Terraform provider knowledge. The `Resource`, `Intent`, `Rego intent`,
reference output, validation errors, plan errors, OPA results and historical
repair traces are excluded. `Rego intent` is accessed only after the final HCL
has been generated and planned.

Local repair receives a Terraform validation/plan diagnostic but never an OPA
policy or failure.
