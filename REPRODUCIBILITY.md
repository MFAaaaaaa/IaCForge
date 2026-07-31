# Reproducibility

## Fixed version chain

Formal v2 experiments use:

```json
{
  "terraform_version": "1.9.8",
  "provider_name": "hashicorp/aws",
  "provider_version": "5.90.0",
  "graph_ir_version": "2.0",
  "contract_version": "2.0",
  "retriever_version": "hybrid-v2"
}
```

Every run additionally records the schema, KG and evidence SHA-256 values.
Runtime code fails closed when the provider constraint differs from `5.90.0`.
Changing provider version requires re-exporting schema, re-parsing matching
documentation, rebuilding KG/dense index/cache and publishing a new manifest.

Historical 5.100.0 CSV results remain historical artifacts; they must not be
mixed with formal v2 comparisons.

## Environment

- Python 3.10+
- Terraform 1.9.8
- AWS Provider 5.90.0
- OPA 0.61.0 or a validated compatible version

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 scripts/verify_package.py --strict
```

## Determinism

Planner decoding defaults to temperature 0, top-p 1 and JSON-object guided
decoding. HCL/repair decoding defaults to temperature 0 and top-p 1. The
OpenAI-compatible client records actual input/output tokens, latency, model
name and request parameters.

The offline and online retrieval paths are compared by canonical JSON SHA-256,
not serialization order. The prompt cache stores retriever/provider versions,
KG/schema hashes, retrieval parameters and candidate scores.

## Offline provider mirror

```bash
./scripts/prepare_provider_mirror.sh /path/to/aws-provider-5.90.0-mirror
```

Verify that the mirror contains exactly 5.90.0 before a formal run.

## Row selection and checkpoints

```bash
ROW_IDS_FILE=/path/to/test_rows.json \
MODE=full MODEL=qwen2.5-coder-3b MAX_ROWS=458 \
RESUME=1 CHECKPOINT_EVERY=1 \
./scripts/run_framework.sh
```

`Evaluation Row ID` is the original zero-based dataset row. Checkpoints are
written atomically after each configured interval.
