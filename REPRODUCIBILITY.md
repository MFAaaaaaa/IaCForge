# Reproducibility

## Environment

- Python 3.10+
- Terraform 1.9.8
- Terraform AWS Provider 5.90.0
- OPA 0.61.0 or a validated compatible version
- OpenAI-compatible model endpoint

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Only required for paper-KG Chroma retrieval/rebuilding.
pip install -r requirements-paper-kg.txt

python3 -m unittest discover -s tests -v
python3 scripts/verify_package.py --strict
```

Formal clean runs use Graph IR 2.0, Provider Contract 2.0, and retriever
`hybrid-v2`. Changing the provider version requires rebuilding the schema,
documentation KG, typed graph, dense index, and offline cache.

## Common Runner

```bash
MODE=full MODEL=qwen2.5-coder-3b MAX_ROWS=458 \
QWEN_BASE_URL=http://127.0.0.1:8000/v1 \
./scripts/run_framework.sh
```

Supported models are listed in `configs/models/`. Key controls:

```text
MAX_ROWS=458
ROW_IDS_FILE=/path/to/row_ids.json
RESUME=1
CHECKPOINT_EVERY=1
QWEN_IR_MAX_TOKENS=1536
QWEN_MAX_TOKENS=2048
QWEN_TEMPERATURE=0
QWEN_TOP_P=1
```

The evaluator dynamically caps an HCL/repair call at 4096 tokens based on IR
resource count. To reproduce a historical result exactly, use the request
parameters stored in that CSV/log; historical runs used several larger output
limits and are retained as immutable artifacts rather than silently rerun.

## Mode Matrix

| Command mode | KG profile | KG stage | Repair |
| --- | --- | --- | ---: |
| `baseline` | none | none | no |
| `ir_only` | none | none | no |
| `ir_schema` | none | none | no |
| `full` | clean multigranular | both | no |
| `full_repair1` | clean multigranular | both | one plan-failure call |
| `paper_ir` | paper | IR | no |
| `paper_hcl` | paper | HCL | no |
| `paper_both` | paper | both | no |
| `paper_both_repair1` | paper | both | one plan-failure call |
| `hybrid_both` | hybrid | both | no |
| `hybrid_both_repair1` | hybrid | both | one plan-failure call |

Paper modes explicitly enable the benchmark-scoped profile. Repair never gets
raw KG evidence or the Provider Contract directly.

## Paper KG Retrieval

The bundled Chroma index uses
`sentence-transformers/all-mpnet-base-v2`. Default resource top-k is 2.
Retrieval starts from Prompt-to-document/resource matches, uses the in-code
BM25/resource-label fallback and hybrid reranking, expands direct resources
through `reference_relations`, and retrieves optional argument/block evidence
from the corresponding Chroma collection.

Expected Chroma counts:

```text
terraform_resources           5996
terraform_doc_chunks          1390
terraform_examples             422
terraform_arguments_blocks    4419
total                         12227
```

The embedding model must already be available locally because the retriever
loads it with `local_files_only=True`.

## Result Integrity

`results/RESULT_MANIFEST.json` is authoritative for selected historical runs.
It stores SHA-256 for each CSV/log and verifies 458 completed rows per CSV.
Run:

```bash
python3 scripts/verify_package.py --strict
sha256sum -c SHA256SUMS
```

The manifest includes the Qwen2.5-Coder 14B
`paperkg/both_localrepair1_no_direct_kg` run completed on 2026-08-04. This run
uses a 32K context window, a maximum output of 16,384 tokens, and one repair
call after plan failure without directly passing raw KG to the repair prompt.
