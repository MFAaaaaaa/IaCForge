# Reproducibility

## Environment

- Python 3.10+
- Terraform 1.9.8-compatible CLI
- AWS provider 5.90.0
- OPA 0.61.0 or compatible
- OpenAI-compatible Qwen2.5-Coder endpoint

Install `requirements.txt`. Install `requirements-paper-kg.txt` when running Paper KG.

Prepare an offline provider mirror when network-free planning is required:

```bash
scripts/prepare_provider_mirror.sh /path/to/terraform-provider-aws
```

## Supported runs

The current entry point exposes only four runs:

| Mode | KG at Planner | KG at Compiler | Local repair |
|---|---|---|---|
| `full_kg` | Full KG evidence | typed Provider Contract | disabled |
| `full_kg_repair` | Full KG evidence | typed Provider Contract | at most one call |
| `paper_kg` | Paper KG evidence | Paper KG evidence | disabled |
| `paper_kg_repair` | Paper KG evidence | Paper KG evidence | at most one call |

Run a small sanity subset first:

```bash
MODE=full_kg MODEL=qwen2.5-coder-3b MAX_ROWS=3 scripts/run_framework.sh
MODE=paper_kg MODEL=qwen2.5-coder-3b MAX_ROWS=3 scripts/run_framework.sh
```

Then run all 458 rows by setting `MAX_ROWS=458`. `ROW_IDS_FILE` selects an explicit ordered subset, `RESUME=1` resumes checkpoints, and `CHECKPOINT_EVERY` controls checkpoint frequency.

`VERIGRAPH_MAX_REPAIR_STEPS` is the repair switch retained from the original runner. It defaults to `0` for `full_kg` and `paper_kg`, and to `1` for `full_kg_repair` and `paper_kg_repair`. Values above `1` are clipped to one repair call.

The retained result artifacts are immutable copies of completed runs. Their byte hashes and measured metrics are recorded in `results/RESULT_MANIFEST.json`.

## Verification

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_package.py --strict
sha256sum -c --quiet SHA256SUMS
```

`scripts/verify_package.py` checks dataset size, KG assets, Paper KG index counts, the repair boundary, retained result/log pairs, model configurations, and forbidden removed paths.
