import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))

import opa_evaluator


@unittest.skipUnless(shutil.which("opa"), "opa executable is not installed")
class OPAIntegrationTests(unittest.TestCase):
    @staticmethod
    def run_command(args, cwd, timeout):
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def evaluate(self, policy):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_text(json.dumps({"resource_changes": []}), encoding="utf-8")
            return opa_evaluator.opa_evaluate(plan, policy, self.run_command)

    def test_nonstandard_package(self):
        success, error = self.evaluate(
            """package custom_benchmark

is_configuration_valid = true
"""
        )
        self.assertTrue(success, error)

    def test_rego_v1_package(self):
        success, error = self.evaluate(
            """package main

import rego.v1

is_configuration_valid if {
    count(input.resource_changes) == 0
}
"""
        )
        self.assertTrue(success, error)


if __name__ == "__main__":
    unittest.main()
