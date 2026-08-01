"""Deterministic pre-validation checks for generated Terraform HCL."""

from __future__ import annotations

import re


VARIABLE_REFERENCE_RE = re.compile(r"\bvar\.([A-Za-z_][A-Za-z0-9_]*)\b")
VARIABLE_BLOCK_RE = re.compile(
    r'\bvariable\s+"([A-Za-z_][A-Za-z0-9_]*)"\s*\{', re.MULTILINE
)


def _without_comments(hcl: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", str(hcl or ""), flags=re.DOTALL)
    return re.sub(r"(?m)^\s*(?:#|//).*?$", "", text)


def referenced_input_variables(hcl: str) -> set[str]:
    return set(VARIABLE_REFERENCE_RE.findall(_without_comments(hcl)))


def declared_input_variables(hcl: str) -> set[str]:
    return set(VARIABLE_BLOCK_RE.findall(_without_comments(hcl)))


def undeclared_input_variables(hcl: str) -> list[str]:
    return sorted(referenced_input_variables(hcl) - declared_input_variables(hcl))


def _balanced_block(text: str, opening_brace: int) -> str:
    depth = 0
    quote = ""
    escaped = False
    for index in range(opening_brace, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening_brace + 1 : index]
    return text[opening_brace + 1 :]


def referenced_variables_without_defaults(hcl: str) -> list[str]:
    text = _without_comments(hcl)
    referenced = referenced_input_variables(text)
    without_defaults = []
    for match in VARIABLE_BLOCK_RE.finditer(text):
        name = match.group(1)
        if name not in referenced:
            continue
        body = _balanced_block(text, match.end() - 1)
        if not re.search(r"\bdefault\s*=", body):
            without_defaults.append(name)
    return sorted(set(without_defaults))


def diagnostics(hcl: str) -> dict[str, list[str]]:
    return {
        "undeclared_input_variables": undeclared_input_variables(hcl),
        "referenced_variables_without_defaults": referenced_variables_without_defaults(
            hcl
        ),
    }


def has_issues(report: dict[str, list[str]]) -> bool:
    return any(report.values())


def render_repair_diagnostic(report: dict[str, list[str]]) -> str:
    lines = [
        "Deterministic HCL safety check failed before Terraform validation.",
        "Replace avoidable var.NAME references with concrete visible-prompt or schema-valid local literals.",
        "If a variable is necessary, declare it in the same program with a concrete default.",
    ]
    for key, values in report.items():
        if values:
            lines.append(f"{key}: {', '.join(values)}")
    return "\n".join(lines)
