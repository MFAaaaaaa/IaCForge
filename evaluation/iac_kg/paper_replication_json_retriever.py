import json
import math
import os
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import provider_schema


# WARNING:
# This retriever reconstructs KG evidence from the paper replication package.
# Its resource universe may be benchmark-scoped, so it is disabled by default
# in full_kg_retriever.py and must not be used for main no-leak paper runs.


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


TOP_K_RESOURCES = _env_int("IAC_KG_PAPER_TOP_K_RESOURCES", 2)
MAX_RESOURCES = _env_int("IAC_KG_PAPER_MAX_RESOURCES", 5)
MAX_REQUIRED_ARGS = _env_int("IAC_KG_PAPER_MAX_REQUIRED_ARGS", 8)
MAX_OPTIONAL_ARGS = _env_int("IAC_KG_PAPER_MAX_OPTIONAL_ARGS", 4)
MAX_REQUIRED_BLOCKS = _env_int("IAC_KG_PAPER_MAX_REQUIRED_BLOCKS", 4)
MAX_OPTIONAL_BLOCKS = _env_int("IAC_KG_PAPER_MAX_OPTIONAL_BLOCKS", 3)
MAX_BLOCK_ARGS = _env_int("IAC_KG_PAPER_MAX_BLOCK_ARGS", 4)
MAX_EXAMPLES = _env_int("IAC_KG_PAPER_MAX_EXAMPLES", 0)
MAX_DEPENDENCIES = _env_int("IAC_KG_PAPER_MAX_DEPENDENCIES", 8)
MAX_HIGH_CONFIDENCE_HINTS = _env_int("IAC_KG_PAPER_MAX_REQUIRED_HINTS", 2)
ENTRYPOINT_DOC_CHUNKS = "terraform_doc_chunks"
ENTRYPOINT_RESOURCE_SUMMARIES = "terraform_resources"
DEFAULT_DOC_CHUNK_DISTANCE_DELTA = 0.6
RETRIEVAL_MODE_PAPER_ORIGINAL = "paper_original"
RETRIEVAL_MODE_FAITHFUL_GRAPH = "faithful_graph"
RETRIEVAL_MODE_HYBRID = "hybrid"
RETRIEVAL_MODE_BM25 = "bm25"
PAPER_ORIGINAL_TOP_K = _env_int("IAC_KG_PAPER_ORIGINAL_TOP_K", 5)
PAPER_OPTIONAL_TOP_K = _env_int("IAC_KG_PAPER_OPTIONAL_TOP_K", 5)
FAITHFUL_CONTEXT_CHAR_BUDGET = _env_int("IAC_KG_FAITHFUL_CONTEXT_CHAR_BUDGET", 18000)

TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "aws",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}
GENERIC_MATCH_TOKENS = {
    "53",
    "aws",
    "configuration",
    "config",
    "policy",
    "resource",
    "resources",
    "route",
    "route53",
    "routing",
    "setting",
    "settings",
}

RESOURCE_RE = re.compile(r"\baws_[A-Za-z0-9_]+\b")
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)
TF_REF_RE = re.compile(
    r"\b(aws_[A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?"
)

NESTED_BLOCK_CONCEPT_HINTS = {
    "aws_elastic_beanstalk_environment": {
        "setting": [
            "elastic beanstalk",
            "beanstalk environment",
            "autoscaling settings",
            "configuration settings",
            "cpu utilization",
            "threshold",
            "instance profile",
            "rds database",
            "database instance",
            "vpc",
            "subnet",
            "security group",
        ],
    },
    "aws_autoscaling_group": {
        "launch_template": ["launch template"],
    },
    "aws_route53_record": {
        "alias": ["alias record", "alias target", "load balancer", "elb", "alb"],
        "weighted_routing_policy": ["weighted routing", "weighted routing policy"],
        "geolocation_routing_policy": ["geolocation", "geolocation routing"],
        "latency_routing_policy": ["latency routing", "closest region"],
        "failover_routing_policy": ["failover routing", "failover record"],
    },
}

RESOURCE_COMPATIBILITY_PHRASES = {
    "aws_route53_traffic_policy": ["traffic policy"],
}


def configured_root():
    value = os.environ.get("IAC_KG_PAPER_REPLICATION_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    value = os.environ.get("IAC_KG_REPLICATION_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (
        Path(__file__).with_name("paper_replication_downloads")
        / "iac-research-without-agent"
    ).resolve()


def configured_chroma_dir(root):
    value = os.environ.get("IAC_KG_PAPER_CHROMA_DIR", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return root / "graphrag_approaches" / "langgraph_GR-Base" / "app" / "paper_rebuilt_chroma"


def configured_retrieval_mode():
    value = os.environ.get(
        "IAC_KG_PAPER_RETRIEVAL_MODE", RETRIEVAL_MODE_PAPER_ORIGINAL
    ).strip().lower()
    if value in {"faithful", "faithful-graph", "paper_faithful", "paper-faithful"}:
        return RETRIEVAL_MODE_FAITHFUL_GRAPH
    if value in {"original", "paper", "paper-original"}:
        return RETRIEVAL_MODE_PAPER_ORIGINAL
    if value in {
        RETRIEVAL_MODE_HYBRID,
        RETRIEVAL_MODE_BM25,
        RETRIEVAL_MODE_PAPER_ORIGINAL,
        RETRIEVAL_MODE_FAITHFUL_GRAPH,
    }:
        return value
    return RETRIEVAL_MODE_PAPER_ORIGINAL


def _tokenize(text):
    normalized = re.sub(r"[^a-z0-9_+.#/-]+", " ", str(text or "").lower())
    normalized = normalized.replace("_", " ").replace("-", " ")
    raw = re.findall(r"[a-z0-9]+", normalized)
    tokens = []
    for idx, token in enumerate(raw):
        if not token or token in TOKEN_STOPWORDS:
            continue
        tokens.append(token)
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            tokens.append(token[:-1])
        if token.startswith("route53"):
            tokens.extend(["route53", "route", "53"])
        if token.startswith("cloudwatch"):
            tokens.extend(["cloudwatch", "cloud", "watch"])
        if token.startswith("apigateway"):
            tokens.extend(["api", "gateway"])
        if token == "elb":
            tokens.extend(["elastic", "load", "balancer"])
        if token == "alb":
            tokens.extend(["application", "load", "balancer"])
        if token == "nlb":
            tokens.extend(["network", "load", "balancer"])
        if token == "route" and idx + 1 < len(raw) and raw[idx + 1] == "53":
            tokens.append("route53")
    return tokens


def _compact(text, limit=900):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _safe_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None


def _iter_blocks(blocks):
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        yield block
        yield from _iter_blocks(block.get("blocks") or [])


def _resource_text(resource_type, record):
    pieces = [
        resource_type,
        record.get("resource", {}).get("description", ""),
        record.get("resource", {}).get("llm_summary", ""),
    ]
    for arg in record.get("arguments", []) or []:
        if isinstance(arg, dict):
            pieces.extend(
                [
                    arg.get("name", ""),
                    arg.get("description", ""),
                    arg.get("llm_summary", ""),
                ]
            )
    for block in _iter_blocks(record.get("blocks") or []):
        pieces.extend(
            [
                block.get("name", ""),
                block.get("description", ""),
                block.get("llm_summary", ""),
            ]
        )
        for arg in block.get("arguments", []) or []:
            if isinstance(arg, dict):
                pieces.extend(
                    [
                        arg.get("name", ""),
                        arg.get("description", ""),
                        arg.get("llm_summary", ""),
                    ]
                )
    for example in record.get("examples", []) or []:
        if isinstance(example, dict):
            pieces.extend(
                [
                    example.get("title", ""),
                    example.get("llm_summary", ""),
                    example.get("code", "")[:1200],
                ]
            )
    return " ".join(str(piece) for piece in pieces if piece)


@lru_cache(maxsize=2)
def _load_paper_replication(root_text):
    root = Path(root_text)
    docs_dir = root / "notebooks_kg_construction" / "terraform_json_docs_with_summaries"
    fallback_dir = root / "notebooks_kg_construction" / "kg_json"
    refs_dir = root / "notebooks_kg_construction" / "reference_relations"

    records = {}
    source_dir = docs_dir if docs_dir.exists() else fallback_dir
    for path in sorted(source_dir.glob("*.json")):
        resource_type = path.stem
        if not provider_schema.resource_type_exists(resource_type):
            continue
        record = _safe_json(path)
        if isinstance(record, dict):
            records[resource_type] = record

    references = defaultdict(list)
    if refs_dir.exists():
        for path in sorted(refs_dir.glob("*.json")):
            resource_type = path.stem
            rows = _safe_json(path)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                target = row.get("target_resource_type")
                if not provider_schema.resource_type_exists(target):
                    continue
                references[resource_type].append(
                    {
                        "argument_path": str(row.get("argument_path") or ""),
                        "references_output": str(row.get("references_output") or ""),
                        "target_resource_type": target,
                        "source": str(path.relative_to(root)),
                    }
                )

    doc_freq = defaultdict(int)
    resource_docs = {}
    for resource_type, record in records.items():
        tokens = Counter(_tokenize(_resource_text(resource_type, record)))
        resource_docs[resource_type] = tokens
        for token in tokens:
            doc_freq[token] += 1

    return {
        "root": str(root),
        "source_dir": str(source_dir),
        "records": records,
        "references": dict(references),
        "resource_docs": resource_docs,
        "doc_freq": dict(doc_freq),
        "n_docs": len(resource_docs),
    }


def _bm25_score(query_tokens, doc_tokens, doc_freq, n_docs):
    score = 0.0
    length = sum(doc_tokens.values()) or 1
    avgdl = 240.0
    k1 = 1.5
    b = 0.75
    for token, qtf in query_tokens.items():
        tf = doc_tokens.get(token, 0)
        if tf <= 0:
            continue
        df = doc_freq.get(token, 0)
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        denom = tf + k1 * (1.0 - b + b * length / avgdl)
        score += idf * (tf * (k1 + 1.0) / denom) * min(2, qtf)
    return score


def _label_score(query_tokens, resource_type):
    query_set = set(query_tokens)
    label_tokens = set(_tokenize(resource_type.replace("aws_", "").replace("_", " ")))
    overlap = label_tokens.intersection(query_set)
    if not overlap:
        return 0.0
    return 7.0 * len(overlap) + 16.0 * (len(overlap) / max(1, len(label_tokens)))


def _search_resources(prompt, kg):
    mode = configured_retrieval_mode()
    if mode == RETRIEVAL_MODE_BM25:
        return _search_resources_bm25(prompt, kg, limit=TOP_K_RESOURCES)

    chroma_disabled = os.environ.get("IAC_KG_PAPER_DISABLE_CHROMA", "0").lower() in {
        "1",
        "true",
        "yes",
    }
    chroma_hits = [] if chroma_disabled else _search_resources_chroma(prompt, kg)
    if mode == RETRIEVAL_MODE_PAPER_ORIGINAL:
        if chroma_hits:
            return chroma_hits[:TOP_K_RESOURCES]
        return _search_resources_bm25(prompt, kg, limit=TOP_K_RESOURCES)

    lexical_hits = _search_resources_bm25(prompt, kg, limit=max(TOP_K_RESOURCES * 3, 8))
    if chroma_hits:
        return _merge_resource_hits(prompt, chroma_hits, lexical_hits)
    return lexical_hits[:TOP_K_RESOURCES]


def _search_resources_bm25(prompt, kg, limit=None):
    query_tokens = Counter(_tokenize(prompt))
    scored = []
    explicit = set(RESOURCE_RE.findall(prompt or ""))
    for resource_type, doc_tokens in kg["resource_docs"].items():
        score = _bm25_score(query_tokens, doc_tokens, kg["doc_freq"], kg["n_docs"])
        score += _label_score(query_tokens, resource_type)
        if resource_type in explicit:
            score += 100.0
        if score > 0:
            scored.append((score, resource_type))
    scored.sort(key=lambda item: (-item[0], item[1]))
    limit = limit or TOP_K_RESOURCES
    return [
        {
            "resource_type": resource_type,
            "score": round(score, 4),
            "entrypoint_collection": "bm25_resource_text",
            "score_semantics": "higher_is_better",
        }
        for score, resource_type in scored[:limit]
    ]


def _merge_resource_hits(prompt, chroma_hits, lexical_hits):
    explicit = set(RESOURCE_RE.findall(prompt or ""))
    candidates = {}
    for rank, hit in enumerate(chroma_hits):
        resource_type = hit["resource_type"]
        candidates.setdefault(resource_type, dict(hit))
        candidates[resource_type]["hybrid_score"] = candidates[resource_type].get(
            "hybrid_score", 0.0
        ) + max(0.0, 40.0 - rank * 3.0)
        candidates[resource_type]["entrypoint_collection"] = hit.get(
            "entrypoint_collection"
        )
    for rank, hit in enumerate(lexical_hits):
        resource_type = hit["resource_type"]
        candidates.setdefault(resource_type, dict(hit))
        candidates[resource_type]["hybrid_score"] = candidates[resource_type].get(
            "hybrid_score", 0.0
        ) + min(35.0, float(hit.get("score", 0.0))) + max(0.0, 16.0 - rank)
    for resource_type, hit in candidates.items():
        if not _resource_compatible_with_prompt(prompt, resource_type):
            hit["hybrid_score"] = hit.get("hybrid_score", 0.0) - 80.0
            continue
        if resource_type in explicit:
            hit["hybrid_score"] = hit.get("hybrid_score", 0.0) + 100.0
        elif _label_supported_by_prompt(prompt, resource_type):
            hit["hybrid_score"] = hit.get("hybrid_score", 0.0) + 35.0
    ranked = sorted(
        candidates.values(),
        key=lambda hit: (-float(hit.get("hybrid_score", 0.0)), hit["resource_type"]),
    )
    merged = []
    for hit in ranked[:TOP_K_RESOURCES]:
        merged.append(
            {
                "resource_type": hit["resource_type"],
                "score": round(float(hit.get("hybrid_score", 0.0)), 4),
                "entrypoint_collection": "hybrid_chroma_bm25",
                "score_semantics": "higher_is_better",
            }
        )
    return merged


def _search_resources_chroma(prompt, kg):
    index_dir = configured_chroma_dir(Path(kg["root"]))
    if not index_dir.exists() or not (index_dir / "chroma.sqlite3").exists():
        return []
    hits = _search_resources_chroma_collection(
        prompt,
        kg,
        str(index_dir),
        ENTRYPOINT_DOC_CHUNKS,
        k=TOP_K_RESOURCES,
        paper_unique_top_k=True,
    )
    if hits:
        return hits
    return _search_resources_chroma_collection(
        prompt,
        kg,
        str(index_dir),
        ENTRYPOINT_RESOURCE_SUMMARIES,
        k=max(TOP_K_RESOURCES * 3, 12),
        paper_unique_top_k=False,
    )


def _search_resources_chroma_collection(
    prompt, kg, index_dir_text, collection_name, k, paper_unique_top_k
):
    try:
        vector_store = _get_vector_store(index_dir_text, collection_name)
        docs_and_scores = vector_store.similarity_search_with_score(
            query=prompt,
            k=k,
        )
    except Exception:
        return []

    hits = []
    seen = set()
    best_score = None
    for doc, score in docs_and_scores:
        resource_type = (
            doc.metadata.get("resource_type")
            or doc.metadata.get("resource_name")
            or doc.metadata.get("name")
        )
        if not provider_schema.resource_type_exists(resource_type):
            continue
        if resource_type in seen or resource_type not in kg["records"]:
            continue
        score = float(score)
        if best_score is None:
            best_score = score
        elif collection_name == ENTRYPOINT_DOC_CHUNKS:
            try:
                max_delta = float(
                    os.environ.get(
                        "IAC_KG_PAPER_DOC_DISTANCE_DELTA",
                        str(DEFAULT_DOC_CHUNK_DISTANCE_DELTA),
                    )
                )
            except ValueError:
                max_delta = DEFAULT_DOC_CHUNK_DISTANCE_DELTA
            if score > best_score + max_delta:
                continue
        elif (
            not paper_unique_top_k
            and len(hits) >= 2
            and score > best_score + max(0.25, best_score)
        ):
            continue
        seen.add(resource_type)
        hits.append(
            {
                "resource_type": resource_type,
                "score": round(float(score), 4),
                "entrypoint_collection": collection_name,
            }
        )
        if len(hits) >= TOP_K_RESOURCES:
            break
    return hits


@lru_cache(maxsize=8)
def _get_vector_store(index_dir_text, collection_name):
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-mpnet-base-v2",
        model_kwargs={"local_files_only": True},
    )
    return Chroma(
        persist_directory=index_dir_text,
        embedding_function=embeddings,
        collection_name=collection_name,
    )


def _item_summary(item):
    return {
        "name": str(item.get("name", "")),
        "type": str(item.get("type", "")),
        "description": _compact(item.get("description") or item.get("llm_summary") or "", 240),
    }


def _block_summary(block, include_optional=False):
    required_args = []
    optional_args = []
    for arg in block.get("arguments", []) or []:
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        if arg.get("required"):
            required_args.append(_item_summary(arg))
        elif include_optional:
            optional_args.append(_item_summary(arg))
    nested_required = []
    for nested in block.get("blocks", []) or []:
        if not isinstance(nested, dict):
            continue
        cardinality = nested.get("cardinality") or [0, 0]
        min_items = cardinality[0] if isinstance(cardinality, list) and cardinality else 0
        if min_items:
            nested_required.append(_block_summary(nested, include_optional=False))
    return {
        "name": str(block.get("name", "")),
        "cardinality": block.get("cardinality") or [0, 0],
        "description": _compact(block.get("description") or block.get("llm_summary") or "", 360),
        "required_args": required_args[:MAX_BLOCK_ARGS],
        "optional_args": optional_args[:MAX_BLOCK_ARGS],
        "required_nested_blocks": nested_required[:MAX_REQUIRED_BLOCKS],
    }


def _provider_top_level_block_min_items(resource_type, block_name, fallback):
    block_spec = provider_schema.nested_block_types(resource_type).get(block_name)
    if isinstance(block_spec, dict):
        try:
            return int(block_spec.get("min_items") or 0)
        except (TypeError, ValueError):
            return 0
    return fallback


def _required_reference_paths(resource_type, record):
    paths = set()
    for arg in record.get("arguments", []) or []:
        if isinstance(arg, dict) and arg.get("required") and arg.get("name"):
            paths.add(str(arg["name"]))

    def visit_block(block, prefix="", parent_required=False):
        block_name = str(block.get("name") or "")
        block_path = f"{prefix}.{block_name}" if prefix and block_name else block_name
        cardinality = block.get("cardinality") or [0, 0]
        min_items = cardinality[0] if isinstance(cardinality, list) and cardinality else 0
        if not prefix:
            min_items = _provider_top_level_block_min_items(
                resource_type, block_name, min_items
            )
        block_required = bool(min_items) if not prefix else bool(parent_required and min_items)
        if block_required and block_path:
            paths.add(block_path)
        for arg in block.get("arguments", []) or []:
            if (
                block_required
                and isinstance(arg, dict)
                and arg.get("required")
                and arg.get("name")
            ):
                arg_path = f"{block_path}.{arg['name']}" if block_path else str(arg["name"])
                paths.add(arg_path)
        for nested in block.get("blocks", []) or []:
            if isinstance(nested, dict):
                visit_block(nested, block_path, block_required)

    for block in record.get("blocks", []) or []:
        if isinstance(block, dict):
            visit_block(block)
    return paths


def _path_matches_reference(reference_path, allowed_paths):
    if not reference_path:
        return False
    if reference_path in allowed_paths:
        return True
    parts = reference_path.split(".")
    for idx in range(1, len(parts)):
        if ".".join(parts[:idx]) in allowed_paths:
            return True
    return False


def _path_match_kind(reference_path, required_paths, optional_paths):
    if _path_matches_reference(reference_path, required_paths):
        return "required"
    if _path_matches_reference(reference_path, optional_paths):
        return "optional"
    return ""


def _search_optional_paths(prompt, resource_type):
    kg = _load_paper_replication(str(configured_root()))
    index_dir = configured_chroma_dir(Path(kg["root"]))
    if not index_dir.exists() or not (index_dir / "chroma.sqlite3").exists():
        return set()
    try:
        vector_store = _get_vector_store(
            str(index_dir), "terraform_arguments_blocks"
        )
        docs_and_scores = vector_store.similarity_search_with_score(
            query=prompt,
            k=5,
            filter={"resource": resource_type},
        )
    except Exception:
        return set()

    paths = set()
    for doc, _score in docs_and_scores:
        path = str(doc.metadata.get("path") or "").strip()
        kind = str(doc.metadata.get("type") or "").strip()
        if not path:
            continue
        paths.add(path)
        if kind == "block" or "." in path:
            paths.add(path.split(".", 1)[0])
    return paths


def _label_supported_by_prompt(prompt, resource_type):
    prompt_tokens = set(_tokenize(prompt))
    label_tokens = [
        token
        for token in _tokenize(resource_type.replace("aws_", "").replace("_", " "))
        if token not in TOKEN_STOPWORDS
    ]
    if not label_tokens:
        return False
    overlap = set(label_tokens).intersection(prompt_tokens)
    distinctive = {token for token in overlap if token not in GENERIC_MATCH_TOKENS}
    return bool(distinctive) and len(overlap) / max(1, len(set(label_tokens))) >= 0.5


def _resource_compatible_with_prompt(prompt, resource_type):
    phrases = RESOURCE_COMPATIBILITY_PHRASES.get(resource_type)
    if not phrases:
        return True
    prompt_text = str(prompt or "").lower()
    return any(phrase in prompt_text for phrase in phrases)


def _high_confidence_direct_types(hits, prompt):
    if not hits:
        return set()
    selected = []
    best_score = float(hits[0].get("score", 0.0))
    higher_is_better = hits[0].get("score_semantics") == "higher_is_better"
    chroma_style = bool(hits[0].get("entrypoint_collection")) and not higher_is_better
    explicit = set(RESOURCE_RE.findall(prompt or ""))
    for index, hit in enumerate(hits):
        resource_type = hit["resource_type"]
        if not _resource_compatible_with_prompt(prompt, resource_type):
            continue
        if index < MAX_HIGH_CONFIDENCE_HINTS:
            selected.append(resource_type)
            continue
        score = float(hit.get("score", 0.0))
        close = score <= best_score + 0.08 if chroma_style else score >= best_score * 0.9
        if close and (
            resource_type in explicit or _label_supported_by_prompt(prompt, resource_type)
        ):
            selected.append(resource_type)
    return set(selected)


def _resource_context(resource_type, record, include_optional_blocks=False):
    required = []
    optional = []
    for arg in record.get("arguments", []) or []:
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        if arg.get("required"):
            required.append(_item_summary(arg))
        else:
            optional.append(_item_summary(arg))

    required_blocks = []
    optional_blocks = []
    for block in record.get("blocks", []) or []:
        if not isinstance(block, dict) or not block.get("name"):
            continue
        cardinality = block.get("cardinality") or [0, 0]
        min_items = cardinality[0] if isinstance(cardinality, list) and cardinality else 0
        if min_items:
            required_blocks.append(_block_summary(block, include_optional=False))
        elif include_optional_blocks:
            optional_blocks.append(_block_summary(block, include_optional=False))

    examples = []
    if MAX_EXAMPLES > 0:
        for index, example in enumerate(record.get("examples", []) or []):
            if isinstance(example, dict) and example.get("code"):
                examples.append(
                    {
                        "name": example.get("title", "Example"),
                        "index": index,
                        "code_excerpt": _compact(example.get("code", ""), 650),
                    }
                )
            if len(examples) >= 1:
                break

    return {
        "type": resource_type,
        "description": _compact(
            record.get("resource", {}).get("description")
            or record.get("resource", {}).get("llm_summary")
            or "",
            360,
        ),
        "required_arguments": required[:MAX_REQUIRED_ARGS],
        "optional_arguments": optional[:MAX_OPTIONAL_ARGS],
        "required_blocks": required_blocks[:MAX_REQUIRED_BLOCKS],
        "optional_blocks": optional_blocks[:MAX_OPTIONAL_BLOCKS],
        "basic_usage_examples": examples,
        "evidence_id": f"paper_replication_json_resource:{resource_type}",
    }


def _nested_block_schema_summary(resource_type, block_name):
    block_types = provider_schema.nested_block_types(resource_type)
    if block_name not in block_types:
        return None
    required = sorted(provider_schema.nested_block_required_attributes(resource_type, block_name))
    attrs = sorted(provider_schema.nested_block_attributes(resource_type, block_name))
    return {
        "name": block_name,
        "required_args": [{"name": attr, "type": ""} for attr in required[:MAX_BLOCK_ARGS]],
        "all_attrs": attrs[:MAX_BLOCK_ARGS],
    }


def _path_block_names(paths):
    names = set()
    for path in paths or set():
        head = str(path or "").split(".", 1)[0].strip()
        if head:
            names.add(head)
    return names


def _concept_block_names(prompt, resource_type):
    prompt_text = str(prompt or "").lower()
    names = set()
    for block_name, triggers in NESTED_BLOCK_CONCEPT_HINTS.get(resource_type, {}).items():
        if any(trigger in prompt_text for trigger in triggers):
            names.add(block_name)
    return names


def _block_name_supported_by_prompt(prompt, block_name):
    prompt_tokens = set(_tokenize(prompt))
    block_tokens = [
        token
        for token in _tokenize(str(block_name or "").replace("_", " "))
        if token not in TOKEN_STOPWORDS
    ]
    if not block_tokens:
        return False
    overlap = set(block_tokens).intersection(prompt_tokens)
    distinctive_tokens = {token for token in block_tokens if token not in GENERIC_MATCH_TOKENS}
    distinctive_overlap = {
        token for token in overlap if token not in GENERIC_MATCH_TOKENS
    }
    if not distinctive_overlap:
        return False
    return len(distinctive_overlap) >= min(2, len(distinctive_tokens))


def _attr_path_supported_by_prompt(prompt, path):
    prompt_tokens = set(_tokenize(prompt))
    attr_tokens = [
        token
        for token in _tokenize(str(path or "").split(".")[-1].replace("_", " "))
        if token not in TOKEN_STOPWORDS
    ]
    overlap = set(attr_tokens).intersection(prompt_tokens)
    return bool({token for token in overlap if token not in GENERIC_MATCH_TOKENS})


def _filter_optional_paths(prompt, resource_type, paths):
    schema_blocks = provider_schema.nested_block_types(resource_type)
    concept_blocks = _concept_block_names(prompt, resource_type)
    filtered = set()
    for path in paths or set():
        path = str(path or "").strip()
        if not path:
            continue
        head = path.split(".", 1)[0]
        if head in schema_blocks:
            if head in concept_blocks or _block_name_supported_by_prompt(prompt, head):
                filtered.add(path)
        elif _attr_path_supported_by_prompt(prompt, path):
            filtered.add(path)
    return filtered


def _schema_nested_block_hints(contexts, optional_paths_by_resource, prompt):
    hints = []
    seen = set()
    for context in contexts:
        resource_type = context["type"]
        schema_blocks = provider_schema.nested_block_types(resource_type)
        block_names = {block["name"] for block in context["required_blocks"]}
        concept_blocks = {
            name for name in _concept_block_names(prompt, resource_type) if name in schema_blocks
        }
        block_names.update(concept_blocks)
        block_names.update(
            name
            for name in _path_block_names(optional_paths_by_resource.get(resource_type, set()))
            if name in schema_blocks
            and (name in concept_blocks or _block_name_supported_by_prompt(prompt, name))
        )
        for block_name in sorted(block_names):
            key = (resource_type, block_name)
            if key in seen:
                continue
            seen.add(key)
            summary = _nested_block_schema_summary(resource_type, block_name)
            if not summary:
                continue
            required_attrs = [arg["name"] for arg in summary["required_args"]]
            hints.append(
                {
                    "evidence_id": f"provider_schema_nested_block:{resource_type}.{block_name}",
                    "resource_type": resource_type,
                    "block": block_name,
                    "required_attrs": required_attrs,
                    "known_attrs": summary["all_attrs"],
                    "syntax_rule": f"use nested block syntax: {block_name} {{ ... }}",
                    "source": "public Terraform AWS provider schema plus prompt-only KG path retrieval",
                }
            )
    return hints[:MAX_REQUIRED_BLOCKS + MAX_OPTIONAL_BLOCKS]


def _expand_references(selected, kg, optional_paths_by_resource=None, prompt=""):
    optional_paths_by_resource = optional_paths_by_resource or {}
    expanded = list(selected)
    seen = set(selected)
    deps = []
    for src in selected:
        required_paths = _required_reference_paths(src, kg["records"].get(src, {}))
        optional_paths = optional_paths_by_resource.get(src, set())
        for ref in kg["references"].get(src, []) or []:
            match_kind = _path_match_kind(
                ref.get("argument_path", ""), required_paths, optional_paths
            )
            if not match_kind:
                continue
            dst = ref["target_resource_type"]
            if dst not in kg["records"]:
                continue
            if (
                match_kind == "optional"
                and dst not in seen
                and not _label_supported_by_prompt(prompt, dst)
            ):
                continue
            deps.append(
                {
                    "from_type": src,
                    "to_type": dst,
                    "attr": ref["argument_path"],
                    "expr_hint": ref["references_output"],
                    "evidence_id": f"paper_replication_ref:{src}:{ref['argument_path']}:{dst}",
                }
            )
            if dst not in seen and len(expanded) < MAX_RESOURCES:
                expanded.append(dst)
                seen.add(dst)
            if len(deps) >= MAX_DEPENDENCIES:
                break
    return expanded, deps[:MAX_DEPENDENCIES]


def _linear_context(contexts):
    sections = []
    for context in contexts:
        lines = [
            f"RESOURCE: {context['type']}",
            f"Description: {context['description']}",
            "REQUIRED ARGUMENTS:",
        ]
        for arg in context["required_arguments"]:
            lines.append(f"- {arg['name']} ({arg['type']}): {arg['description']}")
        lines.append("OPTIONAL ARGUMENTS:")
        for arg in context["optional_arguments"]:
            lines.append(f"- {arg['name']} ({arg['type']})")
        if context["required_blocks"]:
            lines.append("REQUIRED BLOCKS:")
            for block in context["required_blocks"]:
                lines.append(f"{block['name']} (cardinality: {block['cardinality']}):")
                for arg in block["required_args"]:
                    lines.append(f"  - {arg['name']} ({arg['type']}): {arg['description']}")
                for nested in block["required_nested_blocks"]:
                    lines.append(f"  nested {nested['name']} (cardinality: {nested['cardinality']}):")
                    for arg in nested["required_args"]:
                        lines.append(f"    - {arg['name']} ({arg['type']}): {arg['description']}")
        if context["basic_usage_examples"]:
            lines.append("BASIC USAGE EXAMPLE:")
            lines.append(context["basic_usage_examples"][0]["code_excerpt"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _dedupe_preserve_order(values):
    seen = set()
    ordered = []
    for value in values or []:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _paper_chroma_available(kg):
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    return chroma_dir.exists() and (chroma_dir / "chroma.sqlite3").exists()


def _faithful_direct_resource_hits(prompt, kg):
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    if not _paper_chroma_available(kg):
        hits = _search_resources_bm25(prompt, kg, limit=PAPER_ORIGINAL_TOP_K)
        for hit in hits:
            hit["entrypoint_collection"] = "bm25_fallback_no_chroma"
        return hits, "bm25_fallback_no_chroma"
    try:
        vector_store = _get_vector_store(str(chroma_dir), ENTRYPOINT_DOC_CHUNKS)
        docs_and_scores = vector_store.similarity_search_with_score(
            query=prompt,
            k=PAPER_ORIGINAL_TOP_K,
        )
    except Exception:
        hits = _search_resources_bm25(prompt, kg, limit=PAPER_ORIGINAL_TOP_K)
        for hit in hits:
            hit["entrypoint_collection"] = "bm25_fallback_chroma_error"
        return hits, "bm25_fallback_chroma_error"

    hits = []
    seen = set()
    for doc, score in docs_and_scores:
        resource_type = (
            doc.metadata.get("resource_type")
            or doc.metadata.get("resource_name")
            or doc.metadata.get("name")
        )
        if not provider_schema.resource_type_exists(resource_type):
            continue
        if resource_type not in kg["records"] or resource_type in seen:
            continue
        seen.add(resource_type)
        hits.append(
            {
                "resource_type": resource_type,
                "score": round(float(score), 4),
                "entrypoint_collection": ENTRYPOINT_DOC_CHUNKS,
                "source": doc.metadata.get("source", ""),
                "chunk_index": doc.metadata.get("chunk_index", ""),
                "text": _compact(doc.page_content, 500),
            }
        )
    return hits, ENTRYPOINT_DOC_CHUNKS


def _faithful_optional_args_blocks(prompt, resource_type, kg):
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    if not _paper_chroma_available(kg):
        return [], [], "json_fallback_no_chroma"
    try:
        vector_store = _get_vector_store(str(chroma_dir), "terraform_arguments_blocks")
        docs_and_scores = vector_store.similarity_search_with_score(
            query=prompt,
            k=PAPER_OPTIONAL_TOP_K,
            filter={"resource": resource_type},
        )
    except Exception:
        return [], [], "json_fallback_chroma_error"

    optional_arguments = []
    optional_blocks = []
    for doc, _score in docs_and_scores:
        path = str(doc.metadata.get("path") or "").strip()
        kind = str(doc.metadata.get("type") or "").strip()
        if not path:
            continue
        if kind == "argument" and len(path.split(".")) == 1:
            optional_arguments.append(path)
        if kind == "block" or len(path.split(".")) > 1:
            optional_blocks.append(path.split(".", 1)[0])
    return (
        _dedupe_preserve_order(optional_arguments),
        _dedupe_preserve_order(optional_blocks),
        "terraform_arguments_blocks",
    )


def _faithful_example_title(prompt, resource_type, kg):
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    if not _paper_chroma_available(kg):
        return "0", "json_fallback_no_chroma"
    try:
        vector_store = _get_vector_store(str(chroma_dir), "terraform_examples")
        docs = vector_store.similarity_search(
            query=prompt,
            k=1,
            filter={"resource": resource_type},
        )
    except Exception:
        return "0", "json_fallback_chroma_error"
    if not docs:
        return "0", "terraform_examples_empty"
    return str(docs[0].metadata.get("title") or "0"), "terraform_examples"


def _reference_info_for_path(resource_type, path, kg):
    for ref in kg["references"].get(resource_type, []) or []:
        if str(ref.get("argument_path") or "") != str(path or ""):
            continue
        output = str(ref.get("references_output") or "")
        if "." in output:
            referenced_resource_name, referenced_property_name = output.rsplit(".", 1)
        else:
            referenced_resource_name = str(ref.get("target_resource_type") or "")
            referenced_property_name = output
        return {
            "referenced_resource_name": referenced_resource_name,
            "referenced_property_name": referenced_property_name,
            "reference_type": "attribute",
        }
    return None


def _faithful_arg_detail(arg, resource_type, path, kg):
    detail = {
        "name": arg.get("name"),
        "type": arg.get("type", ""),
        "description": arg.get("description", ""),
        "required": bool(arg.get("required")),
    }
    reference_info = _reference_info_for_path(resource_type, path, kg)
    if reference_info:
        detail["reference_info"] = reference_info
    return detail


def _faithful_block_required(block):
    cardinality = block.get("cardinality") or [0, 0]
    return bool(isinstance(cardinality, list) and cardinality and cardinality[0] >= 1)


def _faithful_block_detail(block, resource_type, kg, path_prefix="", include_contents=True):
    block_name = str(block.get("name") or "")
    block_path = f"{path_prefix}.{block_name}" if path_prefix else block_name
    detail = {
        "element_id": f"{resource_type}:{block_path}",
        "name": block_name,
        "description": block.get("description", ""),
        "resource": resource_type,
        "cardinality": block.get("cardinality") or [0, 0],
        "required": _faithful_block_required(block),
        "required_arguments": [],
        "optional_arguments": [],
        "required_nested_blocks": [],
        "optional_nested_blocks": [],
    }
    if not include_contents:
        return detail

    for arg in block.get("arguments", []) or []:
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        arg_path = f"{block_path}.{arg['name']}"
        arg_detail = _faithful_arg_detail(arg, resource_type, arg_path, kg)
        if arg.get("required"):
            detail["required_arguments"].append(arg_detail)
        else:
            detail["optional_arguments"].append(arg_detail)

    for nested in block.get("blocks", []) or []:
        if not isinstance(nested, dict) or not nested.get("name"):
            continue
        nested_detail = _faithful_block_detail(
            nested,
            resource_type,
            kg,
            path_prefix=block_path,
            include_contents=True,
        )
        if nested_detail.get("required"):
            detail["required_nested_blocks"].append(nested_detail)
        else:
            detail["optional_nested_blocks"].append(nested_detail)
    return detail


def _faithful_find_referenced_resources(
    resource_type, identified_optional_args, identified_optional_blocks, kg
):
    record = kg["records"].get(resource_type) or {}
    allowed_paths = set()
    for arg in record.get("arguments", []) or []:
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        if arg.get("required") or arg["name"] in identified_optional_args:
            allowed_paths.add(str(arg["name"]))

    identified_blocks = set(identified_optional_blocks or [])
    for block in record.get("blocks", []) or []:
        if not isinstance(block, dict) or not block.get("name"):
            continue
        block_name = str(block["name"])
        block_is_required = _faithful_block_required(block)
        block_is_identified = block_name in identified_blocks
        if not block_is_required and not block_is_identified:
            continue
        for arg in block.get("arguments", []) or []:
            if isinstance(arg, dict) and arg.get("required") and arg.get("name"):
                allowed_paths.add(f"{block_name}.{arg['name']}")
        if block_is_identified:
            for nested in block.get("blocks", []) or []:
                if not isinstance(nested, dict) or not nested.get("name"):
                    continue
                if not _faithful_block_required(nested):
                    continue
                nested_name = str(nested["name"])
                for arg in nested.get("arguments", []) or []:
                    if isinstance(arg, dict) and arg.get("required") and arg.get("name"):
                        allowed_paths.add(f"{block_name}.{nested_name}.{arg['name']}")

    referenced = []
    deps = []
    for ref in kg["references"].get(resource_type, []) or []:
        argument_path = str(ref.get("argument_path") or "")
        if not _path_matches_reference(argument_path, allowed_paths):
            continue
        target = str(ref.get("target_resource_type") or "")
        if not provider_schema.resource_type_exists(target) or target not in kg["records"]:
            continue
        referenced.append(target)
        deps.append(
            {
                "from_type": resource_type,
                "to_type": target,
                "attr": argument_path,
                "expr_hint": str(ref.get("references_output") or ""),
                "evidence_id": f"paper_faithful_ref:{resource_type}:{argument_path}:{target}",
            }
        )
    return _dedupe_preserve_order(referenced), deps


def _faithful_query_knowledge_graph(
    resource_type, example_title, identified_optional_args, identified_optional_blocks, kg
):
    record = kg["records"].get(resource_type)
    if not record:
        return {"error": f"Resource '{resource_type}' not found"}

    resource_data = {
        "name": resource_type,
        "description": record.get("resource", {}).get("description", ""),
        "required_arguments": [],
        "optional_arguments": [],
        "required_blocks": [],
        "optional_blocks": [],
        "example": None,
        "referenced_resources": [],
    }

    identified_args = set(identified_optional_args or [])
    identified_blocks = set(identified_optional_blocks or [])
    for arg in record.get("arguments", []) or []:
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        detail = _faithful_arg_detail(arg, resource_type, str(arg["name"]), kg)
        if arg.get("required"):
            resource_data["required_arguments"].append(detail)
        else:
            detail["is_identified"] = arg["name"] in identified_args
            resource_data["optional_arguments"].append(detail)
    resource_data["optional_arguments"].sort(
        key=lambda item: (not item.get("is_identified", False), item["name"])
    )

    for block in record.get("blocks", []) or []:
        if not isinstance(block, dict) or not block.get("name"):
            continue
        if _faithful_block_required(block):
            resource_data["required_blocks"].append(
                _faithful_block_detail(block, resource_type, kg, include_contents=True)
            )
        else:
            block_detail = _faithful_block_detail(
                block,
                resource_type,
                kg,
                include_contents=str(block.get("name")) in identified_blocks,
            )
            block_detail["is_identified"] = str(block.get("name")) in identified_blocks
            resource_data["optional_blocks"].append(block_detail)
    resource_data["optional_blocks"].sort(
        key=lambda item: (not item.get("is_identified", False), item["name"])
    )

    if example_title != "0":
        for example in record.get("examples", []) or []:
            if isinstance(example, dict) and example.get("title") == example_title:
                resource_data["example"] = {
                    "name": example.get("title", ""),
                    "code": example.get("code", ""),
                }
                break

    referenced, _deps = _faithful_find_referenced_resources(
        resource_type, identified_optional_args, identified_optional_blocks, kg
    )
    resource_data["referenced_resources"] = referenced
    return resource_data


def _faithful_format_prompt(resource_data):
    if "error" in resource_data:
        return f"Error: {resource_data['error']}"

    lines = []
    lines.append(f"Resource: {resource_data['name']}")
    lines.append(f"Description: {resource_data['description']}\n")

    def append_reference(line, arg):
        if not arg.get("reference_info"):
            return line
        ref = arg["reference_info"]
        return (
            f"{line} [REFERENCES {ref['referenced_resource_name']}."
            f"{ref['referenced_property_name']}]"
        )

    if resource_data.get("required_arguments"):
        lines.append("Required Arguments:")
        for arg in resource_data["required_arguments"]:
            line = f"- {arg['name']} ({arg['type']}): {arg['description']}"
            lines.append(append_reference(line, arg))
        lines.append("")

    optional_args = resource_data.get("optional_arguments", [])
    if optional_args:
        lines.append("Optional Arguments:")
        identified_lines = []
        other_lines = []
        for arg in optional_args:
            if arg.get("is_identified"):
                line = f"- {arg['name']} ({arg['type']}): {arg.get('description', '(No description available)')}"
                identified_lines.append(append_reference(line, arg))
            else:
                other_lines.append(append_reference(f"- {arg['name']} ({arg['type']})", arg))
        if identified_lines:
            lines.append("Identified for detailed view:")
            lines.extend(identified_lines)
            lines.append("Other optional arguments:")
        lines.extend(other_lines)
        lines.append("")

    def format_block_content(block, indent_prefix=""):
        for arg in block.get("required_arguments", []):
            line = f"{indent_prefix}  - {arg['name']} ({arg['type']}):  {arg.get('description', '')}"
            lines.append(append_reference(line, arg))
        if block.get("is_identified") or not indent_prefix:
            if block.get("optional_arguments"):
                lines.append(f"{indent_prefix}  Optional Arguments:")
                for arg in block.get("optional_arguments", []):
                    line = f"{indent_prefix}  - {arg['name']} ({arg['type']})"
                    lines.append(append_reference(line, arg))
        if block.get("required_nested_blocks"):
            lines.append(f"{indent_prefix}  Nested Required Blocks:")
            for nested in block.get("required_nested_blocks", []):
                lines.append(
                    f"{indent_prefix}    - Block: {nested['name']} -  {nested.get('description', '')}"
                )
                for arg in nested.get("required_arguments", []):
                    line = f"{indent_prefix}      - {arg['name']} ({arg['type']}): {arg.get('description', '')}"
                    lines.append(append_reference(line, arg))
                if nested.get("is_identified_nested") or not indent_prefix:
                    if nested.get("optional_arguments"):
                        lines.append(f"{indent_prefix}      Optional Arguments:")
                        for arg in nested.get("optional_arguments", []):
                            line = f"{indent_prefix}      - {arg['name']} ({arg['type']})"
                            lines.append(append_reference(line, arg))
        if block.get("is_identified") or not indent_prefix:
            if block.get("optional_nested_blocks"):
                lines.append(f"{indent_prefix}  Optional Nested Blocks")
                for nested in block.get("optional_nested_blocks", []):
                    lines.append(f"{indent_prefix}    - Block: {nested['name']}")
                    for arg in nested.get("required_arguments", []):
                        line = f"{indent_prefix}      - {arg['name']} ({arg['type']}): {arg.get('description', '')}"
                        lines.append(append_reference(line, arg))
                    if nested.get("optional_arguments"):
                        lines.append(f"{indent_prefix}      Optional Arguments:")
                        for arg in nested.get("optional_arguments", []):
                            line = f"{indent_prefix}      - {arg['name']} ({arg['type']})"
                            lines.append(append_reference(line, arg))

    if resource_data.get("required_blocks"):
        lines.append("Required Blocks:")
        for block in resource_data["required_blocks"]:
            lines.append(f"- Block: {block['name']} -  {block.get('description', '')}")
            format_block_content(block)
            lines.append("")

    optional_blocks = resource_data.get("optional_blocks", [])
    identified_blocks = [block for block in optional_blocks if block.get("is_identified")]
    non_identified_blocks = [block for block in optional_blocks if not block.get("is_identified")]
    if identified_blocks:
        lines.append("Optional Blocks (Identified for Detailed View):")
        for block in identified_blocks:
            lines.append(f"- Block: {block['name']} -  {block.get('description', '(No description available)')}")
            format_block_content(block)
            lines.append("")
    if non_identified_blocks:
        if not identified_blocks:
            lines.append("Optional Blocks (Excluding arguments and nested blocks):")
        else:
            lines.append("Other Optional Blocks (Excluding arguments and nested blocks):")
        for block in non_identified_blocks:
            lines.append(f"- {block['name']}")
        lines.append("")

    if resource_data.get("referenced_resources"):
        lines.append("Referenced Resources:")
        for resource_type in resource_data["referenced_resources"]:
            lines.append(f"- {resource_type}")
        lines.append("")

    example = resource_data.get("example")
    if example and example.get("code"):
        lines.append("Example:")
        lines.append(f"- {example['name']}")
        lines.append(f"```hcl\n{example['code']}\n```")
    return "\n".join(lines)


def _faithful_response_prompt(prompt, direct_resources, referenced_resources, resource_info):
    response_prompt = f"""
    Below is additional context retrieved from Terraform documentation, listing required arguments and blocks for the identified resources. 
    Note that these resources include both required and OPTIONAL elements, with the optional elements that are relevant to the user query being highlighted.

    ## Directly Identified Resources (based on user query):
    {", ".join(direct_resources)}
    """
    if referenced_resources:
        response_prompt += f"""

    ## Referenced Resources (identified through dependencies):
    {", ".join(referenced_resources)}
    """
    response_prompt += """

    ## Resource Details:
    """
    for resource_type in direct_resources:
        response_prompt += (
            f"\n### {resource_type} (directly identified) ###\n"
            f"{resource_info.get(resource_type, 'No info available.')}\n"
        )
    for resource_type in referenced_resources:
        response_prompt += (
            f"\n### {resource_type} (referenced by other resources) ###\n"
            f"{resource_info.get(resource_type, 'No info available.')}\n"
        )
    response_prompt += f"""

    ## User Query: "{prompt}"

    Based on the user instruction and the resource context above, generate a valid and deployable Terraform configuration.
    Always start by breaking down the the user's query and identifying the resources plus configurations needed to fulfill the request in text and then in code.
    """
    return response_prompt


def _budget_faithful_context(text):
    if FAITHFUL_CONTEXT_CHAR_BUDGET <= 0:
        return text
    if len(text) <= FAITHFUL_CONTEXT_CHAR_BUDGET:
        return text
    marker = (
        "\n\n[Context truncated to fit the target LLM context window. "
        "Retrieval and KG traversal were unchanged; only serialized resource details were shortened.]\n"
    )
    budget = max(0, FAITHFUL_CONTEXT_CHAR_BUDGET - len(marker))
    return text[:budget].rstrip() + marker


def _retrieve_paper_faithful_graph_evidence(prompt, kg):
    hits, entrypoint = _faithful_direct_resource_hits(prompt, kg)
    direct_resources = [hit["resource_type"] for hit in hits]
    if not direct_resources:
        return json.dumps({"source_policy": "No paper faithful KG evidence found."})

    optional_by_resource = {}
    optional_sources = {}
    dependency_hints = []
    referenced_resources = []
    for resource_type in direct_resources:
        optional_args, optional_blocks, optional_source = _faithful_optional_args_blocks(
            prompt, resource_type, kg
        )
        optional_by_resource[resource_type] = {
            "arguments": optional_args,
            "blocks": optional_blocks,
        }
        optional_sources[resource_type] = optional_source
        refs, deps = _faithful_find_referenced_resources(
            resource_type, optional_args, optional_blocks, kg
        )
        referenced_resources.extend(refs)
        dependency_hints.extend(deps)

    referenced_resources = [
        resource_type
        for resource_type in _dedupe_preserve_order(referenced_resources)
        if resource_type not in set(direct_resources)
    ]
    all_resources = direct_resources + referenced_resources

    resource_info = {}
    resource_data_by_type = {}
    example_sources = {}
    for resource_type in all_resources:
        optional_args, optional_blocks, optional_source = _faithful_optional_args_blocks(
            prompt, resource_type, kg
        )
        optional_by_resource.setdefault(
            resource_type, {"arguments": optional_args, "blocks": optional_blocks}
        )
        optional_sources.setdefault(resource_type, optional_source)
        example_title, example_source = _faithful_example_title(prompt, resource_type, kg)
        example_sources[resource_type] = example_source
        resource_data = _faithful_query_knowledge_graph(
            resource_type,
            example_title,
            optional_args,
            optional_blocks,
            kg,
        )
        resource_data_by_type[resource_type] = resource_data
        resource_info[resource_type] = _faithful_format_prompt(resource_data)

    paper_context = _faithful_response_prompt(
        prompt, direct_resources, referenced_resources, resource_info
    )
    paper_context = _budget_faithful_context(paper_context)
    direct_set = set(direct_resources)
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    candidate_resources = []
    nested_block_hints = []
    template_examples = []
    for resource_type in all_resources:
        data = resource_data_by_type.get(resource_type) or {}
        identified = optional_by_resource.get(resource_type, {})
        nested_blocks = []
        for block in data.get("required_blocks", []) or []:
            nested_blocks.append(block["name"])
            nested_block_hints.append(
                {
                    "evidence_id": f"paper_faithful_block:{resource_type}.{block['name']}",
                    "resource_type": resource_type,
                    "block": block["name"],
                    "required_attrs": [
                        arg["name"] for arg in block.get("required_arguments", []) or []
                    ],
                    "known_attrs": [
                        arg["name"]
                        for arg in (
                            block.get("required_arguments", [])
                            + block.get("optional_arguments", [])
                        )
                    ][:12],
                    "syntax_rule": f"use nested block syntax: {block['name']} {{ ... }}",
                    "source": "paper faithful KG Resource-HAS_BLOCK-Argument traversal",
                }
            )
        for block in data.get("optional_blocks", []) or []:
            if block.get("is_identified"):
                nested_blocks.append(block["name"])
                nested_block_hints.append(
                    {
                        "evidence_id": f"paper_faithful_identified_block:{resource_type}.{block['name']}",
                        "resource_type": resource_type,
                        "block": block["name"],
                        "required_attrs": [
                            arg["name"] for arg in block.get("required_arguments", []) or []
                        ],
                        "known_attrs": [
                            arg["name"]
                            for arg in (
                                block.get("required_arguments", [])
                                + block.get("optional_arguments", [])
                            )
                        ][:12],
                        "syntax_rule": f"use nested block syntax: {block['name']} {{ ... }}",
                        "source": "paper faithful KG optional-block selection",
                    }
                )
        candidate_resources.append(
            {
                "type": resource_type,
                "evidence_id": f"paper_faithful_resource:{resource_type}",
                "reason": (
                    "Directly selected by the paper GR-Ref top-5 document chunk retrieval."
                    if resource_type in direct_set
                    else "Added by the paper GR-Ref REFERENCES traversal from a directly selected resource."
                ),
                "retrieval_role": "direct" if resource_type in direct_set else "referenced",
                "required_attrs": [
                    arg["name"] for arg in data.get("required_arguments", []) or []
                ],
                "useful_optional_attrs": identified.get("arguments", []),
                "computed_only_attrs": sorted(
                    provider_schema.computed_only_attributes(resource_type)
                )[:10],
                "nested_blocks": sorted(set(nested_blocks)),
            }
        )
        example = data.get("example")
        if example and example.get("code") and MAX_EXAMPLES > 0:
            template_examples.append(
                {
                    "id": f"paper_faithful_example:{resource_type}:{example.get('name', '')}",
                    "resource_types": sorted(set(RESOURCE_RE.findall(example["code"]))),
                    "common_attributes": sorted(set(ASSIGNMENT_RE.findall(example["code"])))[:12],
                    "reference_examples": sorted(
                        {
                            ".".join(part for part in match if part)
                            for match in TF_REF_RE.findall(example["code"])
                        }
                    )[:12],
                    "code_excerpt": _compact(example["code"], 900),
                }
            )

    evidence = {
        "source_policy": (
            "Faithful local replication of the paper GR-Ref Terraform KG pipeline over public Terraform "
            "AWS provider documentation/schema. Retrieval uses only the visible Prompt text. The IaC-Eval "
            "Resource, Intent, Rego intent, Reference output, validation/plan/OPA outputs, generated files, "
            "and repair traces are not used."
        ),
        "retrieval_method": {
            "paper_package_mode": "faithful_graph",
            "paper_original_steps": [
                "Chroma top-5 over Terraform document chunks to identify direct resources",
                "Chroma top-5 over terraform_arguments_blocks filtered by resource to identify relevant optional arguments/blocks",
                "REFERENCES traversal over required arguments/blocks plus identified optionals to add dependency resources",
                "Chroma top-1 over terraform_examples filtered by resource to select an example",
                "Neo4j-equivalent Resource/Argument/Block/Attribute/Example formatting reconstructed from paper JSON and reference_relations",
            ],
            "configured_mode": RETRIEVAL_MODE_FAITHFUL_GRAPH,
            "actual_entrypoint": entrypoint,
            "package_root": kg["root"],
            "source_dir": kg["source_dir"],
            "chroma_dir": str(chroma_dir),
            "chroma_available": _paper_chroma_available(kg),
            "top_k_doc_chunks": PAPER_ORIGINAL_TOP_K,
            "top_k_optional_arguments_blocks": PAPER_OPTIONAL_TOP_K,
            "serialized_context_char_budget": FAITHFUL_CONTEXT_CHAR_BUDGET,
            "optional_sources_by_resource": optional_sources,
            "example_sources_by_resource": example_sources,
        },
        "retrieved_chunks": hits,
        "directly_identified_resources": direct_resources,
        "referenced_resources": referenced_resources,
        "paper_graph_context": paper_context,
        "candidate_resources": candidate_resources,
        "required_resource_hints": [
            {
                "resource_type": resource_type,
                "confidence": "paper_top5_direct",
                "reason": "Directly selected by paper GR-Ref document chunk retrieval.",
                "evidence_id": f"paper_faithful_resource:{resource_type}",
            }
            for resource_type in direct_resources
        ],
        "dependency_hints": dependency_hints,
        "nested_block_hints": nested_block_hints,
        "conflict_hints": [],
        "schema_facts": [],
        "matched_patterns": [],
        "template_examples": template_examples[:MAX_EXAMPLES],
        "kg_triples": [
            {
                "subject": dep["from_type"],
                "predicate": dep["attr"],
                "object": dep["to_type"],
                "expr_hint": dep["expr_hint"],
                "evidence_id": dep["evidence_id"],
            }
            for dep in dependency_hints
        ],
    }
    return json.dumps(evidence, indent=2, sort_keys=True)


def paper_replication_json_available():
    kg = _load_paper_replication(str(configured_root()))
    return bool(kg["records"])


def retrieve_paper_replication_json_evidence(prompt):
    kg = _load_paper_replication(str(configured_root()))
    if configured_retrieval_mode() == RETRIEVAL_MODE_FAITHFUL_GRAPH:
        return _retrieve_paper_faithful_graph_evidence(prompt, kg)
    hits = _search_resources(prompt, kg)
    direct_selected = [hit["resource_type"] for hit in hits]
    if not direct_selected:
        return json.dumps({"source_policy": "No paper replication KG evidence found."})
    high_confidence_direct = _high_confidence_direct_types(hits, prompt)
    optional_paths = {
        resource_type: _filter_optional_paths(
            prompt, resource_type, _search_optional_paths(prompt, resource_type)
        )
        for resource_type in direct_selected
    }
    selected, deps = _expand_references(direct_selected, kg, optional_paths, prompt)
    direct_selected_set = set(direct_selected)
    contexts = [
        _resource_context(resource_type, kg["records"][resource_type])
        for resource_type in selected
        if resource_type in kg["records"]
    ]
    nested_block_hints = _schema_nested_block_hints(contexts, optional_paths, prompt)
    chroma_dir = configured_chroma_dir(Path(kg["root"]))
    using_chroma = chroma_dir.exists() and (chroma_dir / "chroma.sqlite3").exists()
    retrieval_mode = configured_retrieval_mode()
    used_entrypoint = hits[0].get("entrypoint_collection") if hits else ""
    evidence = {
        "source_policy": (
            "Paper replication-package KG over Terraform AWS provider documentation/schema. "
            "Retrieval uses only the visible Prompt text. The IaC-Eval Resource, Intent, Rego intent, "
            "Reference output, validation/plan/OPA outputs, generated files, and repair traces are not used."
        ),
        "retrieval_method": {
            "paper_package_mode": "JSON reconstruction of Graph RAG context from terraform_json_docs_with_summaries, kg_json, and reference_relations",
            "paper_original": "top-5 all-mpnet/Chroma document retrieval -> resource metadata entry points -> Neo4j traversal",
            "configured_mode": retrieval_mode,
            "actual_entrypoint": used_entrypoint,
            "local_entrypoint": (
                "paper-original Chroma resource entrypoint with KG reference expansion"
                if retrieval_mode == RETRIEVAL_MODE_PAPER_ORIGINAL and used_entrypoint != "bm25_resource_text"
                else "BM25 fallback over package resource/entity summaries"
                if retrieval_mode == RETRIEVAL_MODE_PAPER_ORIGINAL
                else "hybrid Chroma/BM25 resource entrypoint"
                if retrieval_mode == RETRIEVAL_MODE_HYBRID
                else "BM25 resource entrypoint"
            ),
            "package_root": kg["root"],
            "source_dir": kg["source_dir"],
            "chroma_dir": str(chroma_dir),
            "chroma_available": using_chroma,
            "top_k_resources": TOP_K_RESOURCES,
            "optional_reference_expansion": "required KG references plus prompt-retrieved optional arguments/blocks, matching the paper GR-Ref expansion strategy",
        },
        "retrieved_chunks": [
            {
                "id": f"paper_replication_resource_hit:{hit['resource_type']}",
                "resource_type": hit["resource_type"],
                "score": hit["score"],
                "entrypoint_collection": hit.get("entrypoint_collection"),
                "text": _compact(_resource_text(hit["resource_type"], kg["records"][hit["resource_type"]]), 350),
            }
            for hit in hits
        ],
        "paper_graph_context": _linear_context(contexts),
        "candidate_resources": [
            {
                "type": context["type"],
                "evidence_id": context["evidence_id"],
                "reason": (
                    "Directly selected by prompt-only paper-package Graph RAG retrieval."
                    if context["type"] in direct_selected_set
                    else "Added by paper-package KG reference expansion from a directly selected resource."
                ),
                "retrieval_role": (
                    "direct" if context["type"] in direct_selected_set else "referenced"
                ),
                "required_attrs": [item["name"] for item in context["required_arguments"]],
                "useful_optional_attrs": [item["name"] for item in context["optional_arguments"]],
                "computed_only_attrs": sorted(provider_schema.computed_only_attributes(context["type"]))[:10],
                "nested_blocks": sorted(
                    {
                        block["name"]
                        for block in context["required_blocks"]
                    }.union(
                        {
                            hint["block"]
                            for hint in nested_block_hints
                            if hint["resource_type"] == context["type"]
                        }
                    )
                ),
            }
            for context in contexts
        ],
        "required_resource_hints": [
            {
                "resource_type": context["type"],
                "confidence": "high",
                "reason": (
                    "Directly selected by prompt-only paper-package Graph RAG retrieval."
                ),
                "evidence_id": context["evidence_id"],
            }
            for context in contexts
            if context["type"] in high_confidence_direct
        ],
        "dependency_hints": deps,
        "nested_block_hints": nested_block_hints,
        "conflict_hints": [],
        "schema_facts": [],
        "matched_patterns": [],
        "template_examples": [
            {
                "id": f"paper_replication_example:{context['type']}:{example['index']}",
                "resource_types": sorted(set(RESOURCE_RE.findall(example["code_excerpt"]))),
                "common_attributes": sorted(set(ASSIGNMENT_RE.findall(example["code_excerpt"])))[:12],
                "reference_examples": sorted(
                    {
                        ".".join(part for part in match if part)
                        for match in TF_REF_RE.findall(example["code_excerpt"])
                    }
                )[:12],
                "code_excerpt": example["code_excerpt"],
            }
            for context in contexts
            for example in context["basic_usage_examples"][:1]
        ][:MAX_EXAMPLES],
        "kg_triples": [
            {
                "subject": dep["from_type"],
                "predicate": dep["attr"],
                "object": dep["to_type"],
                "expr_hint": dep["expr_hint"],
                "evidence_id": dep["evidence_id"],
            }
            for dep in deps
        ],
    }
    return json.dumps(evidence, indent=2, sort_keys=True)
