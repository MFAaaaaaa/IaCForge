import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import graph_ir
import local_repair
import opa_evaluator
import result_metrics
import schema_rag


class GraphIRTests(unittest.TestCase):
    def test_parse_normalizes_resource_dependencies(self):
        raw = """```json
        {
          "resources": [
            {"type": "aws_vpc", "name": "main", "depends_on": []},
            {"type": "aws_subnet", "name": "public", "depends_on": ["aws_vpc.main"]}
          ],
          "dependencies": [
            {"from": "aws_subnet.public", "to": "aws_vpc.main"}
          ],
          "notes": []
        }
        ```"""
        validation = graph_ir.parse_and_validate_graph_ir(raw)
        self.assertTrue(validation.valid, validation.errors)
        self.assertEqual(graph_ir.resource_types(validation.graph), ["aws_vpc", "aws_subnet"])
        self.assertTrue(validation.graph["explicit_dependencies"])

    def test_duplicate_resource_id_is_rejected(self):
        validation = graph_ir.validate_graph_ir(
            {
                "resources": [
                    {"id": "main", "type": "aws_vpc", "kind": "resource"},
                    {"id": "main", "type": "aws_subnet", "kind": "resource"},
                ],
                "dependencies": [],
            }
        )
        self.assertFalse(validation.valid)
        self.assertIn("duplicate resource address/id", " ".join(validation.errors))

    def test_invalid_model_output_becomes_empty_graph(self):
        validation = graph_ir.safe_parse_graph_ir("not JSON")
        self.assertTrue(validation.generation_failure)
        self.assertEqual(validation.graph["resources"], [])


class SchemaRAGTests(unittest.TestCase):
    def test_exact_type_lookup_reports_missing_types(self):
        with (
            mock.patch.object(
                schema_rag.provider_schema,
                "schema_projection_for_graph",
                return_value={
                    "resources": [{"instance_id": "main", "type": "aws_vpc"}],
                    "missing_types": ["aws_not_real"],
                    "negative_constraints": [],
                },
            ),
            mock.patch.object(schema_rag.provider_schema, "render_schema_projection", return_value="SCHEMA"),
            mock.patch.object(schema_rag, "schema_sha256", return_value="abc"),
        ):
            result = schema_rag.retrieve_schema_for_graph(
                {
                    "resources": [
                        {"id": "main", "type": "aws_vpc", "kind": "resource"},
                        {"id": "bad", "type": "aws_not_real", "kind": "resource"},
                    ]
                }
            )
        self.assertEqual(result.retrieved_types, ("aws_vpc",))
        self.assertEqual(result.missing_types, ("aws_not_real",))
        self.assertEqual(result.context, "SCHEMA")


class LocalRepairTests(unittest.TestCase):
    def test_repair_inputs_and_boundary(self):
        repair_prompt = local_repair.build_prompt(
            "Create a VPC.",
            '{"resources": [{"type": "aws_vpc", "name": "main"}]}',
            "aws_vpc schema context",
            'resource "aws_vpc" "main" {}',
            "Error: Missing required argument cidr_block",
        )
        self.assertIn("Terraform diagnostic", repair_prompt)
        policy = local_repair.policy_manifest()
        self.assertEqual(policy["max_calls"], 1)
        self.assertFalse(policy["raw_kg_in_repair"])
        self.assertFalse(policy["provider_contract_in_repair"])
        self.assertFalse(policy["opa_feedback_used"])

    def test_original_repair_switch_is_supported(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(local_repair.configured_max_steps(False), 0)
            self.assertEqual(local_repair.configured_max_steps(True), 1)
        with mock.patch.dict("os.environ", {"VERIGRAPH_MAX_REPAIR_STEPS": "1"}):
            self.assertEqual(local_repair.configured_max_steps(False), 1)
        with mock.patch.dict("os.environ", {"VERIGRAPH_MAX_REPAIR_STEPS": "3"}):
            self.assertEqual(local_repair.configured_max_steps(False), 1)
        with mock.patch.dict("os.environ", {"VERIGRAPH_MAX_REPAIR_STEPS": "0"}):
            self.assertEqual(local_repair.configured_max_steps(False), 0)


class EvaluationHelpersTests(unittest.TestCase):
    def test_opa_payload_and_result_metrics(self):
        payload = {
            "result": [{"expressions": [{"value": {"main": {"allow": True}}}]}]
        }
        self.assertEqual(opa_evaluator.evaluate_opa_payload(payload), (True, ""))
        rows = [
            {
                "LLM Compilable? #0": "True",
                "LLM Plannable? #0": True,
                "LLM Correct? #0": "Success",
            }
        ]
        self.assertEqual(
            result_metrics.summarize_rows(rows),
            {"rows": 1, "validate": 1, "plan": 1, "opa": 1},
        )


if __name__ == "__main__":
    unittest.main()
