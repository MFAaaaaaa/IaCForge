import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import eval_verigraph
import provider_contract
import prompt_templates_verigraph


def model_output(text):
    return mock.Mock(
        text=text,
        model="test-model",
        stage="ir",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        request_parameters={},
    )


def schema_result():
    result = mock.Mock()
    result.context = "SCHEMA_CONTEXT"
    result.projection = {"resources": [], "negative_constraints": []}
    result.as_dict.return_value = {"retrieved_types": ["aws_vpc"]}
    return result


class ProviderContractTests(unittest.TestCase):
    def test_resource_contract_contains_reference_binding(self):
        graph = {
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
        schema = {
            "resources": [
                {
                    "instance_id": "main",
                    "type": "aws_vpc",
                    "kind": "resource",
                    "required_args": ["cidr_block"],
                    "relevant_optional_args": [],
                    "all_computed_attrs": ["id"],
                },
                {
                    "instance_id": "public",
                    "type": "aws_subnet",
                    "kind": "resource",
                    "required_args": ["vpc_id", "cidr_block"],
                    "relevant_optional_args": [],
                    "all_computed_attrs": ["id"],
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
        subnet = next(
            item for item in contract["resource_contracts"] if item["type"] == "aws_subnet"
        )
        self.assertIn("vpc_id", subnet["required_attributes"])


class GenerationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"candidate_resources": [{"type": "aws_vpc"}]}
        self.ir = model_output(
            '{"resources":[{"type":"aws_vpc","name":"main","depends_on":[]}],'
            '"dependencies":[],"notes":[]}'
        )

    def test_full_kg_uses_contract_at_compiler(self):
        contract = {
            "resource_contracts": [{"type": "aws_vpc"}],
            "dependency_contracts": [],
        }
        with (
            mock.patch.object(
                eval_verigraph,
                "_kg_evidence_for_prompt",
                return_value=(self.evidence, {}),
            ) as retrieve,
            mock.patch.object(
                eval_verigraph.models, "generate_with_metadata", return_value=self.ir
            ),
            mock.patch.object(
                eval_verigraph.schema_rag,
                "retrieve_schema_for_graph",
                return_value=schema_result(),
            ),
            mock.patch.object(
                eval_verigraph.provider_contract_builder,
                "build_provider_contract",
                return_value=contract,
            ),
            mock.patch.object(
                eval_verigraph.provider_contract_builder,
                "render_provider_contract",
                return_value="CONTRACT_BODY",
            ),
        ):
            prompt, notes = eval_verigraph.build_generation_prompt(
                "full_kg", "Create a VPC."
            )
        retrieve.assert_called_once_with("Create a VPC.", "full")
        self.assertEqual(notes["retrieval"]["stages"], ["ir", "hcl"])
        self.assertEqual(notes["hcl_kg_input"], "typed_provider_contract")
        self.assertEqual(len(notes["generation_inputs"]), 4)
        self.assertIn("CONTRACT_BODY", prompt)

    def test_paper_kg_uses_raw_evidence_at_compiler(self):
        with (
            mock.patch.object(
                eval_verigraph,
                "_kg_evidence_for_prompt",
                return_value=(self.evidence, {}),
            ) as retrieve,
            mock.patch.object(
                eval_verigraph.models, "generate_with_metadata", return_value=self.ir
            ),
            mock.patch.object(
                eval_verigraph.schema_rag,
                "retrieve_schema_for_graph",
                return_value=schema_result(),
            ),
            mock.patch.object(
                eval_verigraph.provider_contract_builder, "build_provider_contract"
            ) as build_contract,
        ):
            prompt, notes = eval_verigraph.build_generation_prompt(
                "paper_kg", "Create a VPC."
            )
        retrieve.assert_called_once_with("Create a VPC.", "paper")
        build_contract.assert_not_called()
        self.assertEqual(notes["retrieval"]["stages"], ["ir", "hcl"])
        self.assertEqual(notes["hcl_kg_input"], "raw_kg_evidence")
        self.assertEqual(len(notes["generation_inputs"]), 4)
        self.assertIn("aws_vpc", prompt)

    def test_only_four_modes_are_accepted(self):
        with self.assertRaises(ValueError):
            eval_verigraph.build_generation_prompt("paper_ir", "Create a VPC.")

    def test_prompt_templates_match_stage_inputs(self):
        planner = prompt_templates_verigraph.resource_graph_ir_kg_prompt(
            "Create a VPC.", "KG"
        )
        compiler = prompt_templates_verigraph.resource_graph_ir_schema_kg_generation_prompt(
            "Create a VPC.", "IR", "SCHEMA", "KG"
        )
        self.assertIn("Selected KG evidence pack", planner)
        for value in ("Create a VPC.", "IR", "SCHEMA", "KG"):
            self.assertIn(value, compiler)


if __name__ == "__main__":
    unittest.main()
