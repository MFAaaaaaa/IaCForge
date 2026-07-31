import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import versioning


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = (
    ROOT_DIR
    / "data"
    / "leakfree_multigranular_kg"
    / "offline_retrieval"
    / "provider_contract_full458.jsonl"
)


def prompt_sha256(prompt):
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()


def configured_cache_path():
    value = os.environ.get("IAC_OFFLINE_PROVIDER_CONTRACT_CACHE", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    value = os.environ.get("IAC_KG_OFFLINE_CACHE", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return DEFAULT_CACHE.resolve()


@lru_cache(maxsize=4)
def load_offline_provider_contract_entries(cache_path=None):
    path = Path(cache_path).expanduser().resolve() if cache_path else configured_cache_path()
    cache = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid offline KG cache JSONL at {path}:{line_no}") from exc
            key = str(item.get("prompt_sha256", "")).strip()
            evidence = item.get("evidence")
            if not key or evidence is None:
                raise ValueError(f"Offline KG cache entry missing prompt_sha256/evidence at {path}:{line_no}")
            parsed_evidence = json.loads(evidence) if isinstance(evidence, str) else evidence
            normalized = dict(item)
            normalized["evidence"] = parsed_evidence
            normalized.setdefault("retriever_version", "legacy-deterministic-v1")
            normalized.setdefault("provider_version", versioning.AWS_PROVIDER_VERSION)
            normalized.setdefault("kg_sha256", "")
            normalized.setdefault("schema_sha256", "")
            normalized.setdefault("retrieval_parameters", {})
            normalized.setdefault("candidate_scores", [])
            normalized["evidence_sha256"] = versioning.canonical_sha256(parsed_evidence)
            cache[key] = normalized
    return cache


@lru_cache(maxsize=4)
def load_offline_provider_contract_cache(cache_path=None):
    return {
        key: json.dumps(item["evidence"], sort_keys=True)
        for key, item in load_offline_provider_contract_entries(cache_path).items()
    }


def get_offline_provider_contract_entry(prompt, cache_path=None):
    cache = load_offline_provider_contract_entries(
        str(cache_path) if cache_path else None
    )
    return cache.get(prompt_sha256(prompt))


def get_offline_provider_contract_evidence(prompt, cache_path=None):
    entry = get_offline_provider_contract_entry(prompt, cache_path)
    if entry is None:
        return None
    return json.dumps(entry["evidence"], sort_keys=True)


def make_cache_entry(
    prompt,
    evidence,
    *,
    kg_sha256="",
    schema_sha256="",
    retrieval_parameters=None,
):
    parsed = json.loads(evidence) if isinstance(evidence, str) else evidence
    return {
        "prompt_sha256": prompt_sha256(prompt),
        "retriever_version": versioning.RETRIEVER_VERSION,
        "provider_version": versioning.AWS_PROVIDER_VERSION,
        "kg_sha256": kg_sha256,
        "schema_sha256": schema_sha256,
        "retrieval_parameters": retrieval_parameters or {},
        "candidate_scores": parsed.get("candidate_scores", []),
        "evidence_sha256": versioning.canonical_sha256(parsed),
        "evidence": parsed,
    }


def cache_entry_matches_online(entry, online_evidence):
    parsed = (
        json.loads(online_evidence)
        if isinstance(online_evidence, str)
        else online_evidence
    )
    return entry.get("evidence_sha256") == versioning.canonical_sha256(parsed)


def cache_coverage(prompts, cache_path=None):
    cache = load_offline_provider_contract_cache(str(cache_path) if cache_path else None)
    prompt_hashes = [prompt_sha256(prompt) for prompt in prompts]
    missing = [value for value in prompt_hashes if value not in cache]
    return {
        "prompts": len(prompt_hashes),
        "unique_prompts": len(set(prompt_hashes)),
        "cache_entries": len(cache),
        "covered": len(prompt_hashes) - len(missing),
        "missing_prompt_sha256": sorted(set(missing)),
    }
