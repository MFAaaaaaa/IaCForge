# Building The Leakage-Free Multi-Granular KG

Use `build_leakfree_multigranular_kg.py` to rebuild `terraform_aws_5.90.0_public_kg/`.

The script only accepts:

- `--provider-source`: a Terraform AWS provider source checkout containing `website/docs/r` and `website/docs/d`.
- `--schema-json`: the full output of `terraform providers schema -json` for `registry.terraform.io/hashicorp/aws`.
- `--output-dir`: output directory, defaulting to `terraform_aws_5.90.0_public_kg`.

Example:

```bash
cd data/leakfree_multigranular_kg
python3 build_leakfree_multigranular_kg.py \
  --provider-source /path/to/terraform-provider-aws-5.90.0 \
  --schema-json /path/to/aws-provider-schema-5.90.0.json \
  --output-dir terraform_aws_5.90.0_public_kg
```

Leakage constraints:

- No IaC-Eval CSV is accepted.
- No paperKG, paper replication package, benchmark-scoped 199-resource universe, validation output, plan output, OPA output, generated HCL, or repair trace is accepted.
- `target_types` is always the full union of `resource_schemas` and `data_source_schemas` in the AWS provider schema JSON.

The checked-in KG currently has 1,735 full-provider nodes, 3,514 reference edges, and 1,724 copied public provider documentation files.

Materialize the formal stable-ID representation and quality audit after the
base builder:

```bash
python3 ../../scripts/build_typed_kg.py
python3 ../../evaluation/iacforge_cli.py kg-quality \
  --audit-sample ../../results/reference_edge_audit.csv
```

Optionally build the resource-level dense index using a public embedding model:

```bash
python3 ../../scripts/build_dense_index.py
```

## Building Offline Retrieval Cache

The full framework uses a prompt-hash keyed cache so that different models reuse
the same deterministic KG retrieval output. Rebuild it after changing the KG,
retrieval logic, or dataset prompts:

```bash
python3 ../../scripts/build_offline_cache.py
```

The cache builder reads only `data/complete/data.csv` column `Prompt` and the
full-provider public KG. It writes `offline_retrieval/provider_contract_full458.jsonl`
and matching metadata, including retriever/provider versions, KG/schema hashes,
retrieval parameters and candidate scores. It does not read IaC-Eval `Resource`, `Intent`,
`Rego intent`, `Reference output`, validation/plan/OPA feedback, generated HCL,
or feedback traces.
