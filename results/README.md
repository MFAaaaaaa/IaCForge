# Results

This directory retains exactly 24 complete result/log pairs on all 458 benchmark rows. The three non-KG controls isolate the effects of Graph IR and provider-schema grounding; the KG conditions use Qwen2.5-Coder 3B and 14B.

## Baseline

| Model | Validate | Plan | Pass@1 |
|---|---:|---:|---:|
| Qwen2.5-Coder 3B | 77 | 72 | 27 |
| Qwen2.5-Coder 14B | 146 | 99 | 34 |
| Qwen2.5-Coder 32B AWQ | 173 | 141 | 67 |
| Mistral 7B Instruct | 72 | 52 | 13 |
| Qwen3 8B | 69 | 58 | 22 |
| Qwen3 14B | 128 | 115 | 48 |
| CodeLlama 13B Instruct | 148 | 142 | 42 |

## Baseline + IR

| Model | Validate | Plan | Pass@1 |
|---|---:|---:|---:|
| Qwen2.5-Coder 3B | 98 | 96 | 27 |
| Qwen2.5-Coder 14B | 172 | 144 | 59 |

## Baseline + IR + Schema

| Model | Validate | Plan | Pass@1 |
|---|---:|---:|---:|
| Qwen2.5-Coder 3B | 145 | 134 | 45 |
| Qwen2.5-Coder 14B | 277 | 267 | 116 |
| Qwen2.5-Coder 32B AWQ | 273 | 257 | 109 |
| Mistral 7B Instruct | 56 | 52 | 15 |
| Qwen3 8B | 154 | 135 | 58 |
| Qwen3 14B | 225 | 215 | 97 |
| CodeLlama 13B Instruct | 177 | 168 | 63 |

## KG conditions

| Mode | Model | Validate | Plan | Pass@1 |
|---|---|---:|---:|---:|
| Full KG | 3B | 174 | 165 | 61 |
| Full KG | 14B | 338 | 326 | 138 |
| Full KG + local repair | 3B | 233 | 220 | 76 |
| Full KG + local repair | 14B | 376 | 367 | 149 |
| Paper KG | 3B | 209 | 188 | 93 |
| Paper KG | 14B | 330 | 296 | 155 |
| Paper KG + local repair | 3B | 261 | 230 | 102 |
| Paper KG + local repair | 14B | 386 | 351 | 168 |

Paper KG contains benchmark-scoped relation edges, so those rows are leakage-analysis results and are not directly comparable to Full KG as leakage-free evidence.

Each mode/model directory contains `result.csv` and `run.log`. The files were renamed without changing their bytes. `RESULT_MANIFEST.json` records SHA-256 hashes and the metrics above.
