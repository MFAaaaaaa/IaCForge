# Results Archive

`RESULT_MANIFEST.json` is the machine-readable index. Every listed artifact has
458 completed rows plus SHA-256 for its result CSV and matching log.

## Clean Multigranular KG and Ablations

`clean_multigranular_kg_and_ablations/` contains:

- `baseline`: direct Prompt-to-HCL generation;
- `ir_only`: Graph IR without schema, KG, or repair;
- `ir_schema`: Graph IR plus schema, without KG or repair;
- `ir_schema_multigranular_kg`: Graph IR, schema, and clean multigranular KG at
  both generation stages.

Baseline and IR+schema preserve all available models. IR-only and full clean
KG preserve the available Qwen2.5-Coder 3B and 14B results.

## KG and Repair

`kg_repair/` contains:

- paper KG stage selection (`ir`, `hcl`, `both`);
- paper KG 3B/14B IR-only and HCL-only results with no-direct-KG repair;
- paper KG both-stage 3B/14B results with and without repair;
- half-paper/half-full historical results with and without repair;
- clean multigranular KG results with and without repair.

Repair is one call after plan failure only. It does not directly receive raw KG
or a KG-derived Provider Contract. It may receive Graph IR/current HCL that
were produced by KG-enabled upstream stages.

## Both-Stage Paper KG Repair

The Qwen2.5-Coder 14B `paperkg/both_localrepair1_no_direct_kg` run was completed
on 2026-08-04 with a 32K context window and maximum output of 16,384 tokens. It
reports Validate 386/458, Plan 351/458, and Pass@1 168/458. The repair call does
not directly receive raw KG.

## Qwen2.5-Coder Summary

Counts are `Validate / Plan / Pass@1`, each out of 458.

| Family / variant | 3B | 14B |
| --- | ---: | ---: |
| clean baseline | 77 / 72 / 27 | 146 / 99 / 34 |
| IR only | 98 / 96 / 27 | 172 / 144 / 59 |
| IR + schema | 145 / 134 / 45 | 277 / 267 / 116 |
| IR + schema + clean multigranular KG | 174 / 165 / 61 | 338 / 326 / 138 |
| clean multigranular KG + repair | 233 / 220 / 76 | 376 / 367 / 149 |
| paper KG at IR only | 163 / 149 / 65 | 290 / 281 / 142 |
| paper KG at HCL only | 183 / 166 / 73 | 309 / 280 / 131 |
| paper KG at both stages | 209 / 188 / 93 | 330 / 296 / 155 |
| paper KG at IR only + repair | 224 / 198 / 84 | 388 / 373 / 191 |
| paper KG at HCL only + repair | 228 / 210 / 90 | 369 / 347 / 159 |
| paper KG at both + repair | 261 / 230 / 102 | 386 / 351 / 168 |
| half-paper/half-full KG at both | 196 / 183 / 85 | 328 / 308 / 154 |
| half-paper/half-full KG at both + repair | 245 / 230 / 104 | 362 / 348 / 166 |

These are preserved historical artifacts. Consult each CSV/log for its exact
context and output-token settings before treating differences as controlled
comparisons.
