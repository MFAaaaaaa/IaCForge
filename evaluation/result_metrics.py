"""Result-format compatibility and metric summarization."""

from __future__ import annotations

import csv
from pathlib import Path


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


def summarize_csv(path) -> dict[str, int]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return summarize_rows(list(csv.DictReader(handle)))
