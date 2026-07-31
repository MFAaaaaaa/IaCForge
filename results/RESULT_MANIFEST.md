# Result Manifest

All included CSVs are full 458-row runs. Counts are
`Terraform validate / Terraform plan / IaC-Eval OPA`.

Historical CSVs encode OPA outcomes as `Success`, `Failure`, and
`No opa_result`. Counts below were recomputed with
`scripts/summarize_results.py`, which also supports new boolean-format CSVs.

| Setting | Model | Validate / Plan / OPA |
| --- | --- | --- |
| baseline | CodeLlama-13B-Instruct | 148 / 142 / 42 |
| baseline | Mistral-7B-Instruct | 72 / 52 / 13 |
| baseline | Qwen2.5-Coder-14B | 146 / 99 / 34 |
| baseline | Qwen2.5-Coder-32B-AWQ | 173 / 141 / 67 |
| baseline | Qwen2.5-Coder-3B | 77 / 72 / 27 |
| baseline | Qwen3-14B | 128 / 115 / 48 |
| baseline | Qwen3-8B | 69 / 58 / 22 |
| Graph IR + Schema RAG | CodeLlama-13B-Instruct | 177 / 168 / 63 |
| Graph IR + Schema RAG | Mistral-7B-Instruct | 56 / 52 / 15 |
| Graph IR + Schema RAG | Qwen2.5-Coder-14B | 277 / 267 / 116 |
| Graph IR + Schema RAG | Qwen2.5-Coder-32B-AWQ | 273 / 257 / 109 |
| Graph IR + Schema RAG | Qwen2.5-Coder-3B | 145 / 134 / 45 |
| Graph IR + Schema RAG | Qwen3-14B | 225 / 215 / 97 |
| Graph IR + Schema RAG | Qwen3-8B | 154 / 135 / 58 |
| Graph IR + Schema RAG + KG | Qwen2.5-Coder-14B | 338 / 326 / 138 |
| Graph IR + Schema RAG + KG | Qwen2.5-Coder-3B | 174 / 165 / 61 |

The exact CSV paths remain organized under:

- `baseline/<model>/`
- `ir_schema_grounding/<model>/`
- `full_ir_schema_grounding_kg/<model>/`

No completed IR-only ablation is included. Reproduction runs are written under
`results/reruns/<UTC timestamp>/` to protect archived CSVs.
