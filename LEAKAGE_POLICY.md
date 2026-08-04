# Data Leakage Policy

## Clean Generation Boundary

Allowed before generation:

- visible IaC-Eval `Prompt`;
- Graph IR generated from that prompt;
- public Terraform AWS provider schema, documentation, and examples;
- KG nodes/edges constructed only from those public sources;
- deterministic clean retrieval output keyed by prompt SHA-256.

Forbidden before final generation:

- IaC-Eval `Resource`, `Intent`, `Rego intent`, or reference output/HCL;
- validation errors, plan errors, or OPA results from other candidates;
- historical repair traces;
- manually encoded row IDs, hidden expected resources, or policy predicates.

`Rego intent` is evaluation-only and may be read only after final HCL
generation and planning.

## Profile Classification

| Profile | Classification | Use |
| --- | --- | --- |
| `clean_multigranular` | clean/public | Main no-leakage and ablation results |
| `paper` | potentially benchmark-scoped | Paper-replication comparison only |
| `hybrid_cached_evidence_rebuilt_kg` | historical semi-clean | Historical comparison only |

Paper KG is disabled unless `IAC_ALLOW_BENCHMARK_SCOPED_PAPER_KG=1` is set.
Its resource universe comes from the paper replication package and must not be
reported as the main clean KG result.

The hybrid profile preserves evidence from the first construction. The
original raw KG was lost, and the bundled public-provider KG is a second
construction. Its content may differ slightly from the KG that produced the
evidence, though expected aggregate results should remain close. This profile
must not be relabelled as the clean multigranular profile.

## Repair Boundary

Local repair is permitted to use the current candidate's Terraform **plan**
diagnostic. It is not triggered by initial validation failure and is called at
most once.

Repair directly receives:

- visible Prompt;
- generated normalized Graph IR;
- provider schema context;
- current HCL;
- current Terraform plan diagnostic.

Repair does not directly receive raw KG evidence, a KG-derived Provider
Contract, an OPA policy/result, hidden intent, or reference HCL. The Graph IR
and HCL may already reflect an upstream KG-enabled generation stage; therefore
the accurate label is `localrepair1_no_direct_kg`, not `repair_without_any_kg_influence`.

Every repaired row records:

```json
{
  "trigger": "terraform_plan_failed",
  "max_calls": 1,
  "raw_kg_in_repair": false,
  "provider_contract_in_repair": false,
  "opa_feedback_used": false
}
```

## Cache and Tuning Policy

The clean offline cache may use only Prompt plus public KG data and is keyed by
SHA-256 of Prompt text. A miss fails closed unless online retrieval is
explicitly enabled. Resource aliases and semantic rules must encode only
public AWS/Terraform knowledge and must be included in an ablation when making
a fully automatic retrieval claim.

Do not tune prompts, retrieval rules, or compiler behavior on held-out outputs.
Record development row IDs separately from final evaluation rows.

