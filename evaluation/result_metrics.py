"""Result metric summarization."""

from __future__ import annotations

SUCCESS_VALUES = {"true", "success", "1", "yes"}


def is_success(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in SUCCESS_VALUES


def summarize_rows(rows: list[dict]) -> dict[str, int]:
    return {
        "rows": len(rows),
        "validate": sum(is_success(row.get("LLM Compilable? #0")) for row in rows),
        "plan": sum(is_success(row.get("LLM Plannable? #0")) for row in rows),
        "opa": sum(is_success(row.get("LLM Correct? #0")) for row in rows),
    }
