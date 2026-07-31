"""Human audit metrics for provenance-stratified REFERENCES samples."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "valid"}
FALSE_VALUES = {"0", "false", "no", "invalid"}


def _label(value):
    text = str(value or "").strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def cohens_kappa(pairs):
    pairs = [(left, right) for left, right in pairs if left is not None and right is not None]
    if not pairs:
        return 0.0
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum(
        left_counts[value] / len(pairs) * right_counts[value] / len(pairs)
        for value in (True, False)
    )
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def summarize_reference_audit(path: str | Path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    pairs = [
        (_label(row.get("annotator_1_valid")), _label(row.get("annotator_2_valid")))
        for row in rows
    ]
    agreed = [
        left
        for left, right in pairs
        if left is not None and right is not None and left == right
    ]
    provenance = {}
    for row, (left, right) in zip(rows, pairs):
        if left is None or right is None or left != right:
            continue
        key = row.get("provenance", "unknown")
        provenance.setdefault(key, []).append(left)
    return {
        "rows": len(rows),
        "double_annotated": sum(
            left is not None and right is not None for left, right in pairs
        ),
        "cohens_kappa": cohens_kappa(pairs),
        "agreed_precision": sum(agreed) / len(agreed) if agreed else 0.0,
        "precision_by_provenance": {
            key: sum(values) / len(values) for key, values in provenance.items()
        },
    }
