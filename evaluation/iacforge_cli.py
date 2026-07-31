"""Small non-benchmark CLI for retrieval and KG inspection."""

from __future__ import annotations

import argparse
import json

import evidence_projection
from iac_kg.provider_contract_retriever import (
    configured_root,
    retrieval_rule_catalog,
    retrieve_public_provider_contract_evidence,
)
from iac_kg.typed_kg import kg_quality_report, write_reference_audit_sample
from iac_kg.audit_metrics import summarize_reference_audit


def main():
    parser = argparse.ArgumentParser(prog="iacforge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    retrieve = subparsers.add_parser("retrieve")
    retrieve.add_argument("--prompt", required=True)
    retrieve.add_argument(
        "--projection", choices=("full", "planner"), default="planner"
    )

    quality = subparsers.add_parser("kg-quality")
    quality.add_argument("--root", default=str(configured_root()))
    quality.add_argument("--audit-sample")

    rules = subparsers.add_parser("retrieval-rules")
    rules.add_argument("--pretty", action="store_true")
    audit = subparsers.add_parser("audit-metrics")
    audit.add_argument("--csv", required=True)

    args = parser.parse_args()
    if args.command == "retrieve":
        evidence = retrieve_public_provider_contract_evidence(args.prompt)
        value = (
            evidence_projection.project_planner_evidence(evidence)
            if args.projection == "planner"
            else json.loads(evidence)
        )
    elif args.command == "kg-quality":
        value = kg_quality_report(args.root)
        if args.audit_sample:
            write_reference_audit_sample(args.root, args.audit_sample)
            value["audit_sample"] = args.audit_sample
    elif args.command == "retrieval-rules":
        value = retrieval_rule_catalog()
    else:
        value = summarize_reference_audit(args.csv)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
