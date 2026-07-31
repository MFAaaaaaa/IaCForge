"""Build the leakage-free multi-granular Terraform AWS provider KG.

This script intentionally has no dataset, benchmark, paper-KG, validation,
plan, OPA, generated-HCL, or repair-trace input. It enumerates every resource
and data source present in the supplied Terraform AWS provider schema JSON and
joins those full-schema nodes with public provider documentation/examples.
"""

import argparse
import json
import re
import shutil
from pathlib import Path


PROVIDER_KEY = "registry.terraform.io/hashicorp/aws"
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
ARG_LINE_RE = re.compile(r"^\s*[-*]\s+`([^`]+)`\s+-\s*(.*)$")
CODE_FENCE_RE = re.compile(r"```(?:hcl|terraform)?\s*\n(.*?)```", re.DOTALL | re.I)
TF_REF_RE = re.compile(r"\b(aws_[A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?")


REFERENCE_ATTR_HINTS = {
    "acl_id": ("aws_network_acl", "id"),
    "allocation_id": ("aws_eip", "id"),
    "api_id": ("aws_apigatewayv2_api", "id"),
    "application": ("aws_elastic_beanstalk_application", "name"),
    "bucket": ("aws_s3_bucket", "id"),
    "bucket_id": ("aws_s3_bucket", "id"),
    "cloudwatch_log_group_arn": ("aws_cloudwatch_log_group", "arn"),
    "cluster": ("aws_ecs_cluster", "id"),
    "cluster_arn": ("aws_ecs_cluster", "arn"),
    "db_subnet_group_name": ("aws_db_subnet_group", "name"),
    "deployment_id": ("aws_api_gateway_deployment", "id"),
    "file_system_id": ("aws_efs_file_system", "id"),
    "function_arn": ("aws_lambda_function", "arn"),
    "function_name": ("aws_lambda_function", "function_name"),
    "gateway_id": ("aws_internet_gateway", "id"),
    "hosted_zone_id": ("aws_route53_zone", "zone_id"),
    "internet_gateway_id": ("aws_internet_gateway", "id"),
    "key_id": ("aws_kms_key", "id"),
    "kms_key_id": ("aws_kms_key", "id"),
    "listener_arn": ("aws_lb_listener", "arn"),
    "load_balancer_arn": ("aws_lb", "arn"),
    "nat_gateway_id": ("aws_nat_gateway", "id"),
    "network_interface_id": ("aws_network_interface", "id"),
    "policy_arn": ("aws_iam_policy", "arn"),
    "queue_url": ("aws_sqs_queue", "id"),
    "resource_id": ("aws_api_gateway_resource", "id"),
    "rest_api_id": ("aws_api_gateway_rest_api", "id"),
    "role_arn": ("aws_iam_role", "arn"),
    "route_table_id": ("aws_route_table", "id"),
    "route_table_ids": ("aws_route_table", "id"),
    "security_group_id": ("aws_security_group", "id"),
    "security_group_ids": ("aws_security_group", "id"),
    "subnet_id": ("aws_subnet", "id"),
    "subnet_ids": ("aws_subnet", "id"),
    "target_group_arn": ("aws_lb_target_group", "arn"),
    "target_group_arns": ("aws_lb_target_group", "arn"),
    "task_definition": ("aws_ecs_task_definition", "arn"),
    "topic_arn": ("aws_sns_topic", "arn"),
    "transit_gateway_id": ("aws_ec2_transit_gateway", "id"),
    "vpc_id": ("aws_vpc", "id"),
    "vpc_peering_connection_id": ("aws_vpc_peering_connection", "id"),
    "vpc_security_group_ids": ("aws_security_group", "id"),
    "zone_id": ("aws_route53_zone", "zone_id"),
}


def split_attrs(attrs):
    required = []
    optional = []
    computed = []
    for name, spec in sorted((attrs or {}).items()):
        if spec.get("required"):
            required.append(name)
        elif spec.get("optional"):
            optional.append(name)
        elif spec.get("computed"):
            computed.append(name)
    return required, optional, computed


def type_to_string(type_spec):
    if isinstance(type_spec, str):
        return type_spec
    return json.dumps(type_spec, sort_keys=True)


def doc_path_for(docs_root, kind, type_name):
    stem = type_name.removeprefix("aws_") + ".html.markdown"
    subdir = "r" if kind == "resource" else "d"
    path = Path(docs_root) / "website" / "docs" / subdir / stem
    return path if path.exists() else None


def markdown_sections(text):
    matches = list(HEADING_RE.finditer(text or ""))
    sections = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections.append((len(match.group(1)), match.group(2).strip(), text[start:end].strip()))
    return sections


def extract_description(text):
    sections = markdown_sections(text)
    if not sections:
        return ""
    first = sections[0]
    if first[0] == 1:
        return re.sub(r"\s+", " ", first[2]).strip()
    return ""


def section_named(text, name):
    name = name.lower()
    chunks = []
    for level, title, body in markdown_sections(text):
        if level == 2 and name in title.lower():
            chunks.append(body)
    return "\n\n".join(chunks)


def extract_arg_docs(text, section):
    body = section_named(text, section)
    docs = {}
    current_context = "top-level"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("###"):
            current_context = stripped.lstrip("#").strip().lower()
            current_context = current_context.replace(" block", "").replace(":", "")
            current_context = re.sub(r"[^a-z0-9_]+", "_", current_context).strip("_")
            continue
        match = ARG_LINE_RE.match(line)
        if not match:
            continue
        name = match.group(1).strip()
        description = re.sub(r"\s+", " ", match.group(2)).strip()
        docs[(current_context, name)] = description
        docs.setdefault(("top-level", name), description)
    return docs


def extract_examples(text):
    example_body = section_named(text, "example")
    source = example_body or text
    examples = []
    for idx, code in enumerate(CODE_FENCE_RE.findall(source)):
        if "resource " not in code and "data " not in code:
            continue
        examples.append(
            {
                "name": "Basic Usage" if idx == 0 else f"Example {idx + 1}",
                "index": idx,
                "code": code.strip(),
            }
        )
    return examples


def flatten_block_types(resource_type, block_types, doc_arg_docs, path=()):
    blocks = []
    for block_name, spec in sorted((block_types or {}).items()):
        block = spec.get("block", {})
        nesting = spec.get("nesting_mode", "")
        min_items = spec.get("min_items", 0)
        max_items = spec.get("max_items", 0)
        context = block_name.lower()
        attrs = block.get("attributes", {})
        required, optional, computed = split_attrs(attrs)
        blocks.append(
            {
                "name": block_name,
                "id": ".".join(path + (block_name,)),
                "resource": resource_type,
                "cardinality": [min_items, max_items],
                "nesting_mode": nesting,
                "description": doc_arg_docs.get(("top-level", block_name), ""),
                "required_args": [
                    {
                        "name": attr,
                        "type": type_to_string(attrs[attr].get("type")),
                        "description": doc_arg_docs.get((context, attr), doc_arg_docs.get(("top-level", attr), "")),
                    }
                    for attr in required
                ],
                "optional_args": [
                    {
                        "name": attr,
                        "type": type_to_string(attrs[attr].get("type")),
                        "description": doc_arg_docs.get((context, attr), doc_arg_docs.get(("top-level", attr), "")),
                    }
                    for attr in optional
                ],
                "computed_attrs": [
                    {
                        "name": attr,
                        "type": type_to_string(attrs[attr].get("type")),
                        "description": doc_arg_docs.get((context, attr), doc_arg_docs.get(("top-level", attr), "")),
                    }
                    for attr in computed
                ],
            }
        )
        blocks.extend(flatten_block_types(resource_type, block.get("block_types", {}), doc_arg_docs, path + (block_name,)))
    return blocks


def resource_record(type_name, kind, schema, doc_text, doc_relpath):
    block = schema.get("block", {})
    attrs = block.get("attributes", {})
    required, optional, computed = split_attrs(attrs)
    arg_docs = extract_arg_docs(doc_text, "Argument Reference")
    attr_docs = extract_arg_docs(doc_text, "Attribute Reference")
    return {
        "resource_type": type_name,
        "kind": kind,
        "description": extract_description(doc_text),
        "source_doc": doc_relpath,
        "required_args": [
            {
                "name": attr,
                "type": type_to_string(attrs[attr].get("type")),
                "description": arg_docs.get(("top-level", attr), ""),
            }
            for attr in required
        ],
        "optional_args": [
            {
                "name": attr,
                "type": type_to_string(attrs[attr].get("type")),
                "description": arg_docs.get(("top-level", attr), ""),
            }
            for attr in optional
        ],
        "attributes": [
            {
                "name": attr,
                "type": type_to_string(attrs[attr].get("type")),
                "description": attr_docs.get(("top-level", attr), ""),
            }
            for attr in computed
        ],
        "blocks": flatten_block_types(type_name, block.get("block_types", {}), arg_docs),
        "examples": extract_examples(doc_text),
    }


def iter_reference_edges(record, valid_types):
    type_name = record["resource_type"]
    arg_names = [arg["name"] for arg in record["required_args"] + record["optional_args"]]
    for block in record["blocks"]:
        arg_names.extend(arg["name"] for arg in block["required_args"] + block["optional_args"])
    seen = set()
    for attr in sorted(set(arg_names)):
        hint = REFERENCE_ATTR_HINTS.get(attr)
        if not hint:
            continue
        target_type, target_attr = hint
        if target_type not in valid_types or target_type == type_name:
            continue
        expr = f"{attr} = {target_type}.<name>.{target_attr}"
        if attr.endswith("ids") or attr in {"subnet_ids", "security_group_ids", "vpc_security_group_ids", "route_table_ids"}:
            expr = f"{attr} = [{target_type}.<name>.{target_attr}]"
        key = (type_name, target_type, attr, expr)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "from": type_name,
            "to": target_type,
            "attribute": attr,
            "expr_hint": expr,
            "relationship": "REFERENCES",
            "source": "schema_reference_hint",
        }
    for example in record["examples"]:
        code = example.get("code", "")
        for target_type, _, target_attr in TF_REF_RE.findall(code):
            if target_type not in valid_types or target_type == type_name:
                continue
            yield {
                "from": type_name,
                "to": target_type,
                "attribute": "",
                "expr_hint": f"{target_type}.<name>.{target_attr or 'id'}",
                "relationship": "REFERENCES",
                "source": "documentation_example",
            }


def write_jsonl(path, records):
    with Path(path).open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-source", required=True, help="Path to terraform-provider-aws v5.90.0 source root.")
    parser.add_argument("--schema-json", required=True, help="Path to terraform providers schema -json output.")
    parser.add_argument("--output-dir", default="terraform_aws_5.90.0_public_kg")
    args = parser.parse_args()

    provider_source = Path(args.provider_source).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_docs = output_dir / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_docs.mkdir(parents=True, exist_ok=True)

    schema_payload = json.load(Path(args.schema_json).open())
    provider = schema_payload["provider_schemas"][PROVIDER_KEY]
    resource_schemas = provider.get("resource_schemas", {})
    data_schemas = provider.get("data_source_schemas", {})
    target_types = sorted(set(resource_schemas) | set(data_schemas))
    valid_types = set(resource_schemas) | set(data_schemas)
    if set(target_types) != valid_types:
        raise RuntimeError("Internal error: KG target types are not the full provider schema type set.")

    records = []
    missing_docs = []
    missing_schema = []
    copied_docs = []
    for type_name in target_types:
        if type_name in resource_schemas:
            kind = "resource"
            schema = resource_schemas[type_name]
        elif type_name in data_schemas:
            kind = "data_source"
            schema = data_schemas[type_name]
        else:
            missing_schema.append(type_name)
            continue

        doc_path = doc_path_for(provider_source, kind, type_name)
        doc_text = ""
        doc_relpath = ""
        if doc_path is None:
            missing_docs.append(type_name)
        else:
            doc_text = doc_path.read_text(encoding="utf-8", errors="ignore")
            doc_relpath = str(doc_path.relative_to(provider_source))
            dest = output_docs / (type_name + ".md")
            shutil.copyfile(doc_path, dest)
            copied_docs.append(str(dest.relative_to(output_dir)))
        records.append(resource_record(type_name, kind, schema, doc_text, doc_relpath))

    edges = []
    seen_edges = set()
    for record in records:
        for edge in iter_reference_edges(record, valid_types):
            key = (edge["from"], edge["to"], edge["attribute"], edge["expr_hint"], edge["source"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(edge)

    write_jsonl(output_dir / "resources.jsonl", records)
    write_jsonl(output_dir / "kg_edges.jsonl", edges)
    metadata = {
        "source": "Terraform AWS provider official documentation and provider schema",
        "provider_version": "5.90.0",
        "provider_source": str(provider_source),
        "schema_json": str(Path(args.schema_json).resolve()),
        "dataset_csv": None,
        "paper_kg_source": None,
        "leakage_policy": (
            "Public Terraform AWS provider v5.90.0 docs/schema only. No IaC-Eval "
            "Resource, Intent, Rego intent, Reference output, validation result, "
            "plan result, OPA result, or repair trace is used."
        ),
        "construction_policy": (
            "Full-provider construction: target_types is exactly the union of all "
            "resource_schemas and data_source_schemas in the supplied AWS provider schema. "
            "The script does not accept dataset CSVs or paper-KG retrieval artifacts."
        ),
        "target_types": len(target_types),
        "records": len(records),
        "edges": len(edges),
        "copied_docs": len(copied_docs),
        "missing_docs": missing_docs,
        "missing_schema": missing_schema,
        "kg_shape": {
            "entities": ["Resource", "Argument", "Block", "Attribute", "Example"],
            "relationships": ["HAS_ARGUMENT", "HAS_BLOCK", "EXPORTS_ATTRIBUTE", "HAS_EXAMPLE", "REFERENCES"],
        },
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
