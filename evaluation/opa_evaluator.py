"""IaC-Eval-compatible OPA policy evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


def policy_package(rego_text: str) -> str:
    match = re.search(r"^\s*package\s+([^\s]+)", str(rego_text or ""), flags=re.MULTILINE)
    return match.group(1) if match else ""


def policy_uses_rego_v1(rego_text: str) -> bool:
    return bool(re.search(r"^\s*import\s+rego\.v1\s*$", str(rego_text or ""), flags=re.MULTILINE))


def recursive_leaves(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from recursive_leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_leaves(child)
    else:
        yield value


def evaluate_opa_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        value = payload["result"][0]["expressions"][0]["value"]
    except (KeyError, IndexError, TypeError) as exc:
        return False, f"OPA output has no evaluable data document: {exc}"
    leaves = list(recursive_leaves(value))
    if not leaves:
        return False, "OPA data document contains no decision values."
    success = False not in leaves
    return success, "" if success else "At least one IaC-Eval policy decision evaluated to false."


def opa_evaluate(
    plan_json: Path,
    rego_text: str,
    run_command: Callable[[list[str], Path, int], tuple[int, str, str]],
) -> tuple[bool, str]:
    if not str(rego_text or "").strip():
        return False, "missing Rego intent"
    policy = plan_json.with_suffix(".rego")
    policy.write_text(str(rego_text), encoding="utf-8")
    command = ["opa", "eval"]
    if policy_uses_rego_v1(rego_text):
        command.append("--v1-compatible")
    command.extend(
        [
            "--format",
            "json",
            "--input",
            str(plan_json),
            "--data",
            str(policy),
            "data",
        ]
    )
    code, out, err = run_command(command, plan_json.parent, 60)
    if code != 0:
        return False, (out + "\n" + err).strip()
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return False, f"OPA output is not JSON: {exc}; stdout={out[:1000]}"
    success, detail = evaluate_opa_payload(payload)
    package = policy_package(rego_text) or "<missing>"
    if success:
        return True, ""
    return False, f"package={package}; {detail}; output={out[:2000]}"
