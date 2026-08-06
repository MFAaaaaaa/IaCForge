# Leakage Policy

IaCForge exposes two explicitly different KG conditions.

| KG | Construction | Interpretation |
|---|---|---|
| Full KG | Complete AWS provider schema and official provider documentation/examples | Leakage-free method condition |
| Paper KG | Bundled graph/indexes, including benchmark-scoped relation edges | Reported separately from Full KG |

## Generation boundary

Before HCL generation completes, the pipeline may read only:

- the row’s visible `Prompt`;
- the selected KG and its retrieval indexes;
- the bundled AWS provider schema;
- model/runtime configuration.

It must not read the benchmark `Resource`, `Intent`, `Rego intent`, reference output, validation outcome, plan outcome, OPA outcome, another generated candidate, or a repair trace.

`Rego intent` is read only after the final candidate has produced a valid Terraform plan. It is used exclusively by the OPA evaluator.

## Full KG

Full KG is built from the complete provider schema plus official Terraform AWS provider documentation and examples. Its build does not accept the benchmark dataset as a resource-universe or edge source. Runtime retrieval is conditioned only on the visible Prompt.

## Paper KG

Paper KG contains benchmark-scoped relation edges, so its metrics are reported separately and are not evidence for the dataset-independent Full KG claim. Runtime retrieval still uses only the visible Prompt; that restriction does not remove information already encoded in the graph.

## Local repair

Local repair is enabled by `VERIGRAPH_MAX_REPAIR_STEPS=1` or by a repair mode and is limited to one call after an initial validation or planning failure. It may use the current candidate and the corresponding Terraform diagnostic. It cannot access KG evidence, Provider Contract data, hidden benchmark fields, or OPA policy/results.
