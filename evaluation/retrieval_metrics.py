"""Offline resource-linking metrics using gold labels only after retrieval."""

from __future__ import annotations


def _normalize_gold(value):
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    text = str(value or "")
    for separator in (";", ",", "\n"):
        text = text.replace(separator, " ")
    return {item for item in text.split() if item.startswith("aws_")}


def resource_retrieval_metrics(candidate_types, gold_resources, ks=(1, 3, 5, 10)):
    candidates = [str(item) for item in candidate_types]
    gold = _normalize_gold(gold_resources)
    ranks = [index + 1 for index, item in enumerate(candidates) if item in gold]
    result = {
        "candidate_set_size": len(candidates),
        "gold_resource_count": len(gold),
        "gold_resource_not_retrieved": len(gold - set(candidates)),
        "mrr": 1.0 / min(ranks) if ranks else 0.0,
    }
    for k in ks:
        prefix = set(candidates[:k])
        true_positive = len(prefix & gold)
        result[f"recall@{k}"] = true_positive / len(gold) if gold else 0.0
        result[f"precision@{k}"] = true_positive / k
    return result
