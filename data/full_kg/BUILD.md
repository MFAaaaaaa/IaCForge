# Full KG Build

Full KG is built from the complete Terraform AWS provider schema and official provider documentation/examples. The target type set is exactly the union of all `resource_schemas` and `data_source_schemas` in the supplied schema JSON. Benchmark rows and Paper KG assets are not accepted as inputs.

## Build

```bash
python3 build_full_kg.py \
  --provider-source /path/to/terraform-provider-aws \
  --schema-json ../schema_grounding/aws-provider-schema.json \
  --output-dir provider_kg
```

The output contains:

- `provider_kg/resources.jsonl`
- `provider_kg/kg_edges.jsonl`
- `provider_kg/docs/`
- `provider_kg/metadata.json`

The bundled graph contains 1,735 provider type records, 3,514 relation edges, and 1,724 copied documentation files. Eleven provider types have no matching documentation file but remain represented from the schema.

Runtime retrieval uses deterministic resource alias/schema-document phrase matching, matched concept bundles, score ordering, and bounded dependency closure through official/schema edges. The retrieved evidence enters the Planner directly and is converted into a typed Provider Contract for the Compiler.
