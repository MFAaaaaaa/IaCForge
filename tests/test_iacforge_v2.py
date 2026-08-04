import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import evidence_projection
import graph_ir
import hcl_metrics
import ir_schema_checker
import provider_contract
import versioning
from iac_kg import offline_provider_contract_cache


class SafeGraphIRTests(unittest.TestCase):
    def test_invalid_text_is_replaced_by_empty_canonical_ir(self):
        validation = graph_ir.safe_parse_graph_ir("not JSON at all")
        self.assertTrue(validation.generation_failure)
        self.assertEqual(validation.graph["graph_ir_version"], "2.0")
        self.assertEqual(validation.graph["resources"], [])
        self.assertNotIn("not JSON", graph_ir.render_graph_ir(validation.graph))

    def test_typed_field_binding_is_valid(self):
        graph = {
            "graph_ir_version": "2.0",
            "resources": [
                {"id": "main", "type": "aws_vpc", "kind": "resource"},
                {"id": "public", "type": "aws_subnet", "kind": "resource"},
            ],
            "bindings": [
                {
                    "source": {"resource": "public", "path": "vpc_id"},
                    "target": {"resource": "main", "path": "id"},
                    "kind": "attribute_reference",
                }
            ],
            "constraints": [],
            "explicit_dependencies": [],
            "requirements": [],
        }
        validation = graph_ir.validate_graph_ir(graph)
        self.assertTrue(validation.valid, validation.errors)


class EvidenceProjectionTests(unittest.TestCase):
    def test_planner_projection_excludes_compiler_schema_details(self):
        full = {
            "candidate_resources": [
                {
                    "type": "aws_subnet",
                    "score": 12,
                    "matched_by": ["alias:subnet"],
                    "required_attrs": ["vpc_id"],
                    "computed_only_attrs": ["id"],
                }
            ],
            "dependency_hints": [
                {
                    "from_type": "aws_subnet",
                    "to_type": "aws_vpc",
                    "attr": "vpc_id",
                    "target_path": "id",
                    "confidence": 0.98,
                    "source_kind": "official_doc_example",
                }
            ],
            "provider_contract": {
                "resource_contracts": {
                    "aws_subnet": {"required_args": ["vpc_id"], "computed_attrs": ["id"]}
                },
                "prompt_semantic_slots": {"cidr_blocks": ["10.0.1.0/24"]},
            },
        }
        planner = evidence_projection.project_planner_evidence(full)
        rendered = json.dumps(planner)
        self.assertNotIn("computed_attrs", rendered)
        self.assertNotIn("required_args", rendered)
        self.assertEqual(
            planner["dependency_candidates"][0]["relation"],
            "REQUIRES_VALUE_OF_TYPE",
        )


class SchemaCheckerTests(unittest.TestCase):
    def test_high_confidence_source_path_repair_is_recorded(self):
        graph = {
            "graph_ir_version": "2.0",
            "resources": [
                {"id": "main", "type": "aws_vpc", "kind": "resource"},
                {"id": "public", "type": "aws_subnet", "kind": "resource"},
            ],
            "bindings": [
                {
                    "source": {"resource": "public", "path": "vpc"},
                    "target": {"resource": "main", "path": "id"},
                    "kind": "attribute_reference",
                }
            ],
            "constraints": [],
            "explicit_dependencies": [],
            "requirements": [],
        }
        with (
            mock.patch.object(
                ir_schema_checker.provider_schema, "type_exists", return_value=True
            ),
            mock.patch.object(
                ir_schema_checker.provider_schema,
                "is_assignable",
                side_effect=lambda _type, path, *_args: path == "vpc_id",
            ),
            mock.patch.object(
                ir_schema_checker.provider_schema, "is_exported", return_value=True
            ),
            mock.patch.object(
                ir_schema_checker.provider_schema,
                "assignable_attributes",
                return_value={"vpc_id", "cidr_block"},
            ),
            mock.patch.object(
                ir_schema_checker.provider_schema,
                "attribute_type",
                return_value="string",
            ),
            mock.patch.object(
                ir_schema_checker.provider_schema,
                "types_compatible",
                return_value=True,
            ),
        ):
            result = ir_schema_checker.check_graph_ir(graph)
        self.assertEqual(
            result.graph["bindings"][0]["source"]["path"], "vpc_id"
        )
        self.assertEqual(
            result.normalization_actions[0]["code"], "SAFE_SOURCE_PATH_CORRECTION"
        )


class ProviderContractTests(unittest.TestCase):
    def test_instance_contract_and_binding_expression(self):
        graph = {
            "graph_ir_version": "2.0",
            "resources": [
                {"id": "main", "type": "aws_vpc", "kind": "resource"},
                {"id": "public", "type": "aws_subnet", "kind": "resource"},
            ],
            "bindings": [
                {
                    "source": {"resource": "public", "path": "vpc_id"},
                    "target": {"resource": "main", "path": "id"},
                    "kind": "attribute_reference",
                }
            ],
            "constraints": [
                {
                    "target": "public.map_public_ip_on_launch",
                    "operator": "equals",
                    "value": True,
                }
            ],
            "explicit_dependencies": [],
            "requirements": [],
        }
        schema = {
            "resources": [
                {
                    "instance_id": "main",
                    "type": "aws_vpc",
                    "kind": "resource",
                    "required_args": ["cidr_block"],
                    "relevant_optional_args": [],
                    "all_computed_attrs": ["id", "arn"],
                },
                {
                    "instance_id": "public",
                    "type": "aws_subnet",
                    "kind": "resource",
                    "required_args": ["vpc_id", "cidr_block"],
                    "relevant_optional_args": ["map_public_ip_on_launch"],
                    "all_computed_attrs": ["id", "arn"],
                },
            ],
            "negative_constraints": [],
        }
        with mock.patch.object(
            provider_contract.provider_schema, "is_assignable", return_value=True
        ):
            contract = provider_contract.build_provider_contract(
                "Create a public subnet.", graph, schema, {}
            )
        subnet = contract["instance_contracts"]["public"]
        self.assertEqual(
            subnet["must_assign"]["vpc_id"]["expression"], "aws_vpc.main.id"
        )
        self.assertTrue(
            subnet["should_assign"]["map_public_ip_on_launch"]["value"]
        )
        self.assertIn("id", subnet["forbidden_assignments"])


class HCLMetricTests(unittest.TestCase):
    def test_ir_realization_and_extra_resource_metrics(self):
        graph = {
            "resources": [
                {"id": "main", "type": "aws_vpc", "kind": "resource"},
                {"id": "public", "type": "aws_subnet", "kind": "resource"},
            ],
            "bindings": [
                {
                    "source": {"resource": "public", "path": "vpc_id"},
                    "target": {"resource": "main", "path": "id"},
                }
            ],
        }
        hcl = '''
resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }
resource "aws_subnet" "public" {
  vpc_id = aws_vpc.main.id
}
resource "aws_s3_bucket" "extra" {}
'''
        metrics = hcl_metrics.analyze_hcl(hcl, graph)
        self.assertEqual(metrics["ir_node_realized"], 2)
        self.assertEqual(metrics["ir_binding_realized"], 1)
        self.assertEqual(len(metrics["extra_resources"]), 1)


class CacheAndVersionTests(unittest.TestCase):
    def test_cache_comparison_uses_canonical_json(self):
        evidence = {"b": 2, "a": [1]}
        entry = offline_provider_contract_cache.make_cache_entry("p", evidence)
        self.assertTrue(
            offline_provider_contract_cache.cache_entry_matches_online(
                entry, '{"a":[1],"b":2}'
            )
        )

    def test_version_drift_is_rejected(self):
        with mock.patch.dict(
            "os.environ", {"AWS_PROVIDER_VERSION_CONSTRAINT": "= 5.100.0"}
        ):
            with self.assertRaises(ValueError):
                versioning.assert_version_alignment("5.90.0")


if __name__ == "__main__":
    unittest.main()
