import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


def _schema_file():
    value = os.environ.get("IAC_SCHEMA_FILE", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "data" / "schema_grounding" / "aws-provider-schema.json").resolve()


SCHEMA_FILE = _schema_file()
PROVIDER_KEY = "registry.terraform.io/hashicorp/aws"
@lru_cache(maxsize=1)
def load_aws_schema():
    with SCHEMA_FILE.open() as f:
        data = json.load(f)
    return data["provider_schemas"][PROVIDER_KEY]


def _split_attrs(attrs):
    required = []
    optional = []
    computed = []
    for name, spec in sorted(attrs.items()):
        if spec.get("required"):
            required.append(name)
        elif spec.get("optional"):
            optional.append(name)
        elif spec.get("computed"):
            computed.append(name)
    return required, optional, computed


def type_block(type_name, kind=None):
    schema = type_schema(type_name, kind)
    if not schema:
        return None
    return schema.get("block", {})


def type_schema(type_name, kind=None):
    aws = load_aws_schema()
    if kind == "resource":
        return aws.get("resource_schemas", {}).get(type_name)
    if kind == "data_source":
        return aws.get("data_source_schemas", {}).get(type_name)
    return aws.get("resource_schemas", {}).get(type_name) or aws.get(
        "data_source_schemas", {}
    ).get(type_name)


def type_kind(type_name):
    aws = load_aws_schema()
    if type_name in aws.get("resource_schemas", {}):
        return "resource"
    if type_name in aws.get("data_source_schemas", {}):
        return "data_source"
    return ""


def type_exists(type_name, kind=None):
    return type_schema(type_name, kind) is not None


def attribute_sets(type_name, kind=None):
    block = type_block(type_name, kind)
    if not block:
        return set(), set(), set()
    attrs = block.get("attributes", {})
    required, optional, computed = _split_attrs(attrs)
    return set(required), set(optional), set(computed)


def supported_attributes(type_name, kind=None):
    required, optional, computed = attribute_sets(type_name, kind)
    return required | optional | computed


def assignable_attributes(type_name, kind=None):
    required, optional, _ = attribute_sets(type_name, kind)
    return required | optional


def required_attributes(type_name, kind=None):
    required, _, _ = attribute_sets(type_name, kind)
    return required


def computed_only_attributes(type_name, kind=None):
    _, _, computed = attribute_sets(type_name, kind)
    return computed


def nested_block_types(type_name, kind=None):
    block = type_block(type_name, kind)
    if not block:
        return {}
    return block.get("block_types", {})


def nested_block_attributes(type_name, block_name, kind=None):
    block_spec = nested_block_types(type_name, kind).get(block_name, {})
    attrs = block_spec.get("block", {}).get("attributes", {})
    return set(attrs.keys())


def nested_block_required_attributes(type_name, block_name, kind=None):
    block_spec = nested_block_types(type_name, kind).get(block_name, {})
    attrs = block_spec.get("block", {}).get("attributes", {})
    required, _, _ = _split_attrs(attrs)
    return set(required)


def attribute_spec(type_name, attr_name, kind=None):
    block = type_block(type_name, kind)
    if not block:
        return {}
    return block.get("attributes", {}).get(attr_name, {})


def nested_block_attribute_spec(type_name, block_name, attr_name, kind=None):
    block_spec = nested_block_types(type_name, kind).get(block_name, {})
    attrs = block_spec.get("block", {}).get("attributes", {})
    return attrs.get(attr_name, {})


def attribute_type(type_name, attr_name, kind=None):
    return attribute_spec(type_name, attr_name, kind).get("type")


def nested_block_attribute_type(type_name, block_name, attr_name, kind=None):
    return nested_block_attribute_spec(
        type_name, block_name, attr_name, kind
    ).get("type")


def is_collection_type(type_spec):
    return isinstance(type_spec, list) and bool(type_spec) and type_spec[0] in {
        "list",
        "set",
        "tuple",
    }


def resource_type_exists(type_name):
    aws = load_aws_schema()
    return type_name in aws.get("resource_schemas", {})


def data_source_type_exists(type_name):
    aws = load_aws_schema()
    return type_name in aws.get("data_source_schemas", {})


def is_assignable(type_name, path, kind=None):
    root = str(path or "").split(".", 1)[0]
    return root in assignable_attributes(type_name, kind) or root in nested_block_types(
        type_name, kind
    )


def is_exported(type_name, path, kind=None):
    root = str(path or "").split(".", 1)[0]
    return root in supported_attributes(type_name, kind)


def schema_contract_for_instance(instance, relevant_paths=()):
    """Return a structured, IR-guided exact schema projection."""

    type_name = str(instance.get("type", "")).strip()
    kind = str(instance.get("kind") or type_kind(type_name) or "resource")
    schema = type_schema(type_name, kind)
    if not schema:
        return None
    block = schema.get("block", {})
    attrs = block.get("attributes", {})
    required, optional, computed = _split_attrs(attrs)
    relevant_roots = {
        str(path).split(".", 1)[0]
        for path in relevant_paths
        if str(path or "").strip()
    }
    relevant_optional = sorted(set(optional) & relevant_roots)
    relevant_computed = sorted(set(computed) & relevant_roots)
    relevant_blocks = []
    for name, spec in sorted(block.get("block_types", {}).items()):
        if name not in relevant_roots:
            continue
        nested_attrs = spec.get("block", {}).get("attributes", {})
        nested_required, nested_optional, nested_computed = _split_attrs(nested_attrs)
        relevant_blocks.append(
            {
                "name": name,
                "nesting_mode": spec.get("nesting_mode", ""),
                "min_items": spec.get("min_items", 0),
                "max_items": spec.get("max_items", 0),
                "required_args": nested_required,
                "relevant_optional_args": nested_optional,
                "computed_attrs": nested_computed,
            }
        )
    included = sorted(set(required) | set(relevant_optional) | set(relevant_computed))
    return {
        "instance_id": instance.get("id", ""),
        "type": type_name,
        "kind": kind,
        "required_args": required,
        "relevant_optional_args": relevant_optional,
        "computed_attrs": relevant_computed,
        "all_computed_attrs": computed,
        "arg_types": {
            name: _type_label(attrs.get(name, {}).get("type"))
            for name in included
        },
        "nested_blocks": relevant_blocks,
    }


def schema_projection_for_graph(graph, prompt=""):
    """Build a compact schema contract using only IR/prompt-relevant fields."""

    paths_by_instance = {}
    for binding in graph.get("bindings", []):
        if not isinstance(binding, dict):
            continue
        for endpoint in ("source", "target"):
            value = binding.get(endpoint, {})
            if isinstance(value, dict):
                paths_by_instance.setdefault(str(value.get("resource", "")), set()).add(
                    str(value.get("path", ""))
                )
    for constraint in graph.get("constraints", []):
        if not isinstance(constraint, dict):
            continue
        target = str(constraint.get("target", ""))
        if "." in target:
            instance_id, path = target.split(".", 1)
            paths_by_instance.setdefault(instance_id, set()).add(path)

    prompt_tokens = set(re.findall(r"[a-z0-9]+", str(prompt or "").lower()))
    contracts = []
    missing = []
    for instance in graph.get("resources", []):
        if not isinstance(instance, dict) or instance.get("kind") == "external_input":
            continue
        instance_id = str(instance.get("id", ""))
        type_name = str(instance.get("type", ""))
        kind = str(instance.get("kind", "resource"))
        paths = set(paths_by_instance.get(instance_id, set()))
        for attr in assignable_attributes(type_name, kind):
            attr_tokens = set(attr.split("_"))
            if attr_tokens and attr_tokens <= prompt_tokens:
                paths.add(attr)
        contract = schema_contract_for_instance(instance, paths)
        if contract is None:
            missing.append(type_name)
        else:
            contracts.append(contract)
    return {
        "retrieval_method": "ir_guided_exact_schema_grounding",
        "resources": contracts,
        "missing_types": sorted(set(missing)),
        "negative_constraints": [
            f"Do not generate {type_name} because it is absent from the configured AWS provider schema."
            for type_name in sorted(set(missing))
        ],
    }


def render_schema_projection(projection):
    return json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True)
