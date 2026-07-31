import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import graph_ir
import opa_evaluator
import result_metrics
import schema_rag
from iac_kg import offline_provider_contract_cache


class GraphIRTests(unittest.TestCase):
    def test_parse_validate_and_resource_types(self):
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
        self.assertTrue(validation.valid)
        self.assertEqual(
            graph_ir.resource_types(validation.graph), ["aws_vpc", "aws_subnet"]
        )

    def test_duplicate_resource_is_rejected(self):
        graph = {
            "resources": [
                {"type": "aws_vpc", "name": "main"},
                {"type": "aws_vpc", "name": "main"},
            ],
            "dependencies": [],
        }
        validation = graph_ir.validate_graph_ir(graph)
        self.assertFalse(validation.valid)
        self.assertIn("duplicate resource address", " ".join(validation.errors))


class SchemaRAGTests(unittest.TestCase):
    def test_exact_type_lookup_reports_missing_types(self):
        with (
            mock.patch.object(
                schema_rag.provider_schema,
                "resource_type_exists",
                side_effect=lambda value: value == "aws_vpc",
            ),
            mock.patch.object(
                schema_rag.provider_schema,
                "schema_context_for_types",
                return_value="SCHEMA",
            ),
            mock.patch.object(schema_rag, "schema_sha256", return_value="abc"),
        ):
            result = schema_rag.retrieve_schema(["aws_vpc", "aws_not_real"])
        self.assertEqual(result.retrieved_types, ("aws_vpc",))
        self.assertEqual(result.missing_types, ("aws_not_real",))
        self.assertEqual(result.context, "SCHEMA")


class OPACompatibilityTests(unittest.TestCase):
    def test_arbitrary_package_data_document(self):
        payload = {
            "result": [
                {
                    "expressions": [
                        {
                            "value": {
                                "main": {
                                    "is_valid_bucket": True,
                                    "is_configuration_valid": True,
                                }
                            }
                        }
                    ]
                }
            ]
        }
        self.assertEqual(opa_evaluator.evaluate_opa_payload(payload), (True, ""))

    def test_false_leaf_fails(self):
        payload = {
            "result": [
                {"expressions": [{"value": {"custom": {"a": True, "b": False}}}]}
            ]
        }
        success, detail = opa_evaluator.evaluate_opa_payload(payload)
        self.assertFalse(success)
        self.assertIn("false", detail)

    def test_rego_v1_detection(self):
        self.assertTrue(
            opa_evaluator.policy_uses_rego_v1(
                "package main\n\nimport rego.v1\nallow if { true }\n"
            )
        )


class ResultMetricTests(unittest.TestCase):
    def test_historical_and_new_success_formats(self):
        rows = [
            {
                "LLM Compilable? #0": "True",
                "LLM Plannable? #0": True,
                "LLM Correct? #0": "Success",
            },
            {
                "LLM Compilable? #0": False,
                "LLM Plannable? #0": "False",
                "LLM Correct? #0": False,
            },
        ]
        self.assertEqual(
            result_metrics.summarize_rows(rows),
            {"rows": 2, "validate": 1, "plan": 1, "opa": 1},
        )


class OfflineCacheTests(unittest.TestCase):
    def test_cache_coverage_uses_prompt_hash(self):
        prompt = "Create a VPC."
        entry = {
            "prompt_sha256": offline_provider_contract_cache.prompt_sha256(prompt),
            "evidence": {"candidate_resources": ["aws_vpc"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.jsonl"
            path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
            offline_provider_contract_cache.load_offline_provider_contract_cache.cache_clear()
            coverage = offline_provider_contract_cache.cache_coverage(
                [prompt, "missing"], path
            )
        self.assertEqual(coverage["covered"], 1)
        self.assertEqual(len(coverage["missing_prompt_sha256"]), 1)


if __name__ == "__main__":
    unittest.main()
