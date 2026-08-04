# IaCForge

IaCForge is the clean, paper-facing package for modular Terraform AWS
generation and evaluation. It separates Graph IR, provider-schema grounding,
three KG profiles, HCL generation, one-shot local repair, and post-generation
evaluation.

This directory was reconstructed on 2026-08-04 from trusted archives and the
historical `iac-eval` workspace. It does not depend on the previously damaged
`/home/fameng/zzzhong/IaCForge` contents.

## Pipeline

```text
Visible Prompt
  -> optional KG retrieval/projection for the IR stage
  -> LLM -> Graph IR
  -> optional provider-schema grounding
  -> optional KG-derived Provider Contract for the HCL stage
  -> LLM -> HCL
  -> normalize -> terraform validate -> terraform plan
  -> optional one-shot local repair after plan failure
  -> final validate/plan -> OPA evaluation
```

Local repair does **not directly receive KG evidence or a KG-derived Provider
Contract**. Its inputs are the visible Prompt, generated Graph IR, provider
schema context, current HCL, and Terraform plan diagnostic. Because Graph IR
and current HCL were generated upstream, they may retain indirect influence
from a KG-enabled run. OPA policy/results are never repair inputs.

## Experiment Families

### Clean multigranular KG and ablations

Stored under `results/clean_multigranular_kg_and_ablations/`:

| Variant | Graph IR | Schema | clean multigranular KG |
| --- | ---: | ---: | ---: |
| `baseline` | no | no | no |
| `ir_only` | yes | no | no |
| `ir_schema` | yes | yes | no |
| `ir_schema_multigranular_kg` | yes | yes | IR and HCL |

`baseline` and `ir_schema` contain Qwen2.5-Coder 3B/14B plus the available
additional models. The other two variants contain the available 3B and 14B
runs.

### KG and repair

Stored under `results/kg_repair/`:

- `injection_stage_paperkg/{ir,hcl,both}`: paper KG injection-stage study.
- `paperkg/ir_localrepair1_no_direct_kg` and
  `paperkg/hcl_localrepair1_no_direct_kg`: historical stage-specific paper KG
  repair runs for both 3B and 14B.
- `paperkg/{both,both_localrepair1_no_direct_kg}`: both-stage paper KG without
  repair for 3B/14B, plus the available 3B both-stage repair run.
- `half_paper_half_fullkg/`: historical hybrid variants, including repair.
- `clean_multigranular_kg/{both,both_localrepair1}`: clean KG with and without
  repair.

Every available result has its matching log. `results/RESULT_MANIFEST.json`
records source paths, SHA-256 digests, completion counts, and Validate/Plan/
Pass@1 counts. Paper KG has 3B/14B results for both-stage no-repair and for the
historical IR-only/HCL-only repair variants. The exact 14B **both-stage** paper
KG plus no-direct-KG repair artifact was not found and is explicitly listed as
missing; no stage-specific result was substituted for it.

## KG Profiles

Set `IAC_KG_PROFILE` to one of:

- `clean_multigranular`: public Terraform AWS 5.90.0 docs/schema, typed nodes,
  typed edges, and prompt-keyed offline retrieval cache.
- `paper`: paper replication JSON and Chroma retrieval. This profile may be
  benchmark-scoped and requires `IAC_ALLOW_BENCHMARK_SCOPED_PAPER_KG=1`.
- `hybrid_cached_evidence_rebuilt_kg`: first-construction evidence paired with
  a second KG reconstruction after the original raw KG was lost.

For KG-enabled runs, set `IAC_KG_INJECTION_STAGE=ir`, `hcl`, or `both`.
See `data/README.md`, `data/paper_kg/PROVENANCE.md`, and
`data/hybrid_paper_fullkg/PROVENANCE.md` before comparing profiles.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Needed for paper-KG Chroma retrieval/rebuilding.
pip install -r requirements-paper-kg.txt

python3 -m unittest discover -s tests -v
python3 scripts/verify_package.py --strict

MODE=full MODEL=qwen2.5-coder-3b MAX_ROWS=458 \
  ./scripts/run_framework.sh
```

Additional modes are `baseline`, `ir_only`, `ir_schema`, `full_repair1`,
`paper_ir`, `paper_hcl`, `paper_both`, `paper_both_repair1`, `hybrid_both`, and
`hybrid_both_repair1`. Model endpoint and token settings are controlled by the
`QWEN_*` environment variables documented in `REPRODUCIBILITY.md`.

## Package Map

- `evaluation/`: modular generation, retrieval, repair, and evaluation code.
- `scripts/run_framework.sh`: common experiment launcher.
- `scripts/assemble_results.py`: deterministic result archive assembly.
- `scripts/verify_package.py`: data, leakage-boundary, and result integrity
  checks.
- `data/`: benchmark data, schema, all three KG profiles, and provenance.
- `results/`: selected full-458 results and logs.
- `ARCHITECTURE.md`: module flow and stage contracts.
- `LEAKAGE_POLICY.md`: generation, repair, and evaluation boundaries.
- `REPRODUCIBILITY.md`: environment and exact run commands.
