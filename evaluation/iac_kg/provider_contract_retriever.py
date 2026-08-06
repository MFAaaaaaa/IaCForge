"""Task-conditioned public Terraform provider contract retriever.

This retriever is intentionally not a paper-KG replica. It derives typed
resource contracts and dependency templates from the self-built public AWS
provider docs/schema package plus the bundled provider schema. Runtime
selection uses only the visible prompt.
"""

import json
import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import provider_schema
import versioning


MAX_RESOURCES = int(os.environ.get("IAC_CONTRACT_MAX_RESOURCES", "12"))
MAX_DEPENDENCIES = int(os.environ.get("IAC_CONTRACT_MAX_DEPENDENCIES", "24"))
MAX_OPTIONAL_ATTRS = int(os.environ.get("IAC_CONTRACT_MAX_OPTIONAL_ATTRS", "14"))
MAX_COMPUTED_ATTRS = int(os.environ.get("IAC_CONTRACT_MAX_COMPUTED_ATTRS", "12"))
MAX_BLOCKS = int(os.environ.get("IAC_CONTRACT_MAX_BLOCKS", "10"))
MAX_VALUE_BINDINGS = int(os.environ.get("IAC_CONTRACT_MAX_VALUE_BINDINGS", "20"))
ALLOW_SINGLE_LABEL_TYPES = {
    "aws_elb",
    "aws_eip",
    "aws_lb",
    "aws_nat_gateway",
    "aws_s3_bucket",
    "aws_subnet",
    "aws_vpc",
}

TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "aws",
    "be",
    "by",
    "create",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "name",
    "named",
    "of",
    "on",
    "or",
    "provision",
    "resource",
    "resources",
    "that",
    "the",
    "this",
    "to",
    "using",
    "with",
}

EXTRA_RESOURCE_ALIASES = {
    "aws_acm_certificate": ["acm certificate", "ssl certificate", "tls certificate"],
    "aws_cloudwatch_log_group": ["cloudwatch log group", "log group"],
    "aws_cloudwatch_log_resource_policy": ["cloudwatch log resource policy", "log resource policy"],
    "aws_db_instance": ["rds", "rds instance", "database", "database instance", "db instance", "db_instances"],
    "aws_db_subnet_group": ["db subnet group", "database subnet group"],
    "aws_dynamodb_kinesis_streaming_destination": ["kinesis streaming destination", "dynamodb streaming destination"],
    "aws_dynamodb_table": ["dynamodb table", "dynamo table"],
    "aws_eip": ["elastic ip", "eip", "allocation id"],
    "aws_elastic_beanstalk_application": ["elastic beanstalk application", "beanstalk application"],
    "aws_elastic_beanstalk_application_version": ["elastic beanstalk application version", "beanstalk application version"],
    "aws_elastic_beanstalk_environment": ["elastic beanstalk environment", "beanstalk environment", "worker environment"],
    "aws_elb": ["classic load balancer", "elastic load balancer", "elb"],
    "aws_iam_instance_profile": ["iam instance profile", "instance profile"],
    "aws_iam_policy": ["iam policy", "managed policy"],
    "aws_iam_role": ["iam role", "execution role", "service role"],
    "aws_internet_gateway": ["internet gateway", "igw"],
    "aws_kinesis_stream": ["kinesis stream"],
    "aws_lambda_function": ["lambda function", "serverless function"],
    "aws_lb": ["load balancer", "application load balancer", "network load balancer", "alb", "nlb"],
    "aws_lb_listener": ["load balancer listener", "listener"],
    "aws_lb_target_group": ["target group", "load balancer target group"],
    "aws_nat_gateway": ["nat gateway"],
    "aws_network_acl": ["network acl", "nacl"],
    "aws_network_acl_rule": ["network acl rule", "nacl rule"],
    "aws_route53_query_log": ["route53 query log", "route 53 query log", "query log"],
    "aws_route53_record": ["route53 record", "route 53 record", "dns record", "a record", "cname record"],
    "aws_route53_zone": ["route53 zone", "route 53 zone", "hosted zone"],
    "aws_route53_zone_association": ["route53 zone association", "hosted zone association"],
    "aws_route": ["route table route", "default route", "internet route"],
    "aws_route_table": ["route table"],
    "aws_route_table_association": ["route table association", "associate route table"],
    "aws_s3_bucket": ["s3 bucket", "bucket"],
    "aws_s3_bucket_policy": ["bucket policy", "s3 policy"],
    "aws_s3_bucket_public_access_block": ["public access block", "block public access"],
    "aws_s3_bucket_server_side_encryption_configuration": ["bucket encryption", "server side encryption", "sse"],
    "aws_s3_bucket_versioning": ["bucket versioning", "versioning"],
    "aws_s3_bucket_website_configuration": ["bucket website", "static website", "website hosting"],
    "aws_security_group": ["security group", "firewall", "ingress", "egress", "allow port"],
    "aws_security_group_rule": ["security group rule", "ingress rule", "egress rule"],
    "aws_subnet": ["subnet", "public subnet", "private subnet"],
    "aws_vpc": ["vpc", "virtual private cloud"],
    "aws_vpc_peering_connection": ["vpc peering", "vpc peering connection"],
}

RESOURCE_ALIASES = {
    "aws_vpc": ["vpc", "virtual private cloud"],
    "aws_subnet": ["subnet", "public subnet", "private subnet"],
    "aws_internet_gateway": ["internet gateway", "igw"],
    "aws_route_table": ["route table"],
    "aws_route": ["default route", "internet route", "route table route"],
    "aws_route_table_association": ["route table association", "associate route table"],
    "aws_security_group": ["security group", "ingress", "egress", "firewall", "allow port"],
    "aws_instance": ["ec2", "ec2 instance", "virtual machine", "ami"],
    "aws_s3_bucket": ["s3", "bucket"],
    "aws_s3_bucket_versioning": ["versioning", "bucket versioning"],
    "aws_s3_bucket_server_side_encryption_configuration": ["encryption", "server side encryption"],
    "aws_s3_bucket_public_access_block": ["public access block", "block public access"],
    "aws_s3_bucket_website_configuration": ["website", "static website"],
    "aws_s3_bucket_policy": ["bucket policy"],
    "aws_lambda_function": ["lambda", "function", "serverless"],
    "aws_cloudwatch_log_group": ["cloudwatch log", "cloudwatch log group", "log group"],
    "aws_cloudwatch_log_resource_policy": ["cloudwatch log resource policy", "log resource policy"],
    "aws_iam_role": ["iam role", "assume role", "execution role"],
    "aws_iam_policy": ["iam policy", "policy document"],
    "aws_iam_role_policy_attachment": ["policy attachment", "attach policy", "managed policy"],
    "aws_iam_instance_profile": ["instance profile"],
    "aws_api_gateway_rest_api": ["api gateway", "rest api"],
    "aws_api_gateway_resource": ["api resource"],
    "aws_api_gateway_method": ["api method"],
    "aws_api_gateway_integration": ["api integration", "lambda integration"],
    "aws_lambda_permission": ["lambda permission"],
    "aws_elb": ["elb", "elastic load balancer", "classic load balancer"],
    "aws_lb": ["load balancer", "application load balancer", "network load balancer", "alb", "nlb"],
    "aws_db_instance": [
        "rds",
        "rds instance",
        "database",
        "database instance",
        "db instance",
        "db_instances",
        "mysql",
        "postgres",
        "mariadb",
    ],
    "aws_db_subnet_group": ["db subnet group", "database subnet group"],
    "aws_route53_zone": ["route53 zone", "hosted zone"],
    "aws_route53_record": ["route53 record", "dns record"],
    "aws_route53_zone_association": [
        "route53 zone association",
        "route 53 zone association",
        "zone association",
        "hosted zone association",
    ],
    "aws_route53_query_log": ["route53 query log", "route 53 query log", "query log"],
    "aws_elastic_beanstalk_application": ["elastic beanstalk application", "beanstalk application"],
    "aws_elastic_beanstalk_environment": ["elastic beanstalk environment", "beanstalk environment"],
}

CONCEPT_BUNDLES = [
    {
        "id": "route53_query_log",
        "triggers": ["route53 query log", "route 53 query log", "query log"],
        "resources": [
            "aws_route53_zone",
            "aws_cloudwatch_log_group",
            "aws_cloudwatch_log_resource_policy",
            "aws_route53_query_log",
        ],
    },
    {
        "id": "route53_elb_alias_record",
        "triggers": ["route53 record", "route 53 record", "dns record", "elastic load balancer", "load balancer alias"],
        "resources": ["aws_route53_zone", "aws_route53_record", "aws_elb"],
    },
    {
        "id": "route53_weighted_record",
        "triggers": ["weighted routing", "weighted record", "weighted routing policy"],
        "resources": ["aws_route53_zone", "aws_route53_record"],
    },
    {
        "id": "route53_zone_association",
        "triggers": ["zone association", "hosted zone association", "associate hosted zone"],
        "resources": ["aws_vpc", "aws_route53_zone", "aws_route53_zone_association"],
    },
    {
        "id": "vpc_public_subnet_route",
        "triggers": ["public subnet", "internet gateway", "public route", "internet access"],
        "resources": [
            "aws_vpc",
            "aws_subnet",
            "aws_internet_gateway",
            "aws_route_table",
            "aws_route",
            "aws_route_table_association",
        ],
    },
    {
        "id": "nat_gateway",
        "triggers": ["nat gateway", "elastic ip allocation"],
        "resources": ["aws_eip", "aws_nat_gateway", "aws_subnet"],
    },
    {
        "id": "rds_in_vpc",
        "triggers": ["rds in vpc", "db subnet group", "database subnet group"],
        "resources": ["aws_vpc", "aws_subnet", "aws_db_subnet_group", "aws_db_instance"],
    },
    {
        "id": "lambda_execution_role",
        "triggers": ["lambda function", "lambda execution role", "serverless function"],
        "resources": ["aws_iam_role", "aws_lambda_function"],
    },
    {
        "id": "dynamodb_kinesis_destination",
        "triggers": ["kinesis streaming destination", "dynamodb stream destination"],
        "resources": ["aws_dynamodb_table", "aws_kinesis_stream", "aws_dynamodb_kinesis_streaming_destination"],
    },
    {
        "id": "s3_controls",
        "triggers": ["bucket versioning", "bucket encryption", "public access block", "bucket policy", "static website"],
        "resources": [
            "aws_s3_bucket",
            "aws_s3_bucket_versioning",
            "aws_s3_bucket_server_side_encryption_configuration",
            "aws_s3_bucket_public_access_block",
            "aws_s3_bucket_policy",
            "aws_s3_bucket_website_configuration",
        ],
    },
]

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
    "policy_arn": ("aws_iam_policy", "arn"),
    "queue_url": ("aws_sqs_queue", "id"),
    "resource_id": ("aws_api_gateway_resource", "id"),
    "rest_api_id": ("aws_api_gateway_rest_api", "id"),
    "role": ("aws_iam_role", "name"),
    "role_arn": ("aws_iam_role", "arn"),
    "route_table_id": ("aws_route_table", "id"),
    "route_table_ids": ("aws_route_table", "id"),
    "security_group_id": ("aws_security_group", "id"),
    "security_group_ids": ("aws_security_group", "id"),
    "subnet_id": ("aws_subnet", "id"),
    "subnet_ids": ("aws_subnet", "id"),
    "target_group_arn": ("aws_lb_target_group", "arn"),
    "target_group_arns": ("aws_lb_target_group", "arn"),
    "topic_arn": ("aws_sns_topic", "arn"),
    "vpc_id": ("aws_vpc", "id"),
    "vpc_security_group_ids": ("aws_security_group", "id"),
    "zone_id": ("aws_route53_zone", "zone_id"),
}

INVALID_RESOURCE_ALIASES = {
    "aws_route53_record_set": "aws_route53_record",
    "aws_route53_weighted_routing_policy": "aws_route53_record.weighted_routing_policy",
    "aws_rds_instance": "aws_db_instance",
    "aws_instance_profile": "aws_iam_instance_profile",
}

TF_REF_RE = re.compile(r"\b(aws_[A-Za-z0-9_]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?")
RESOURCE_BLOCK_RE = re.compile(
    r'resource\s+"(?P<type>aws_[A-Za-z0-9_]+)"\s+"(?P<name>[^"]+)"\s*\{(?P<body>.*?)\n\}',
    re.DOTALL,
)
ASSIGNMENT_WITH_REF_RE = re.compile(
    r"^\s*(?P<attr>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*=\s*(?P<expr>.*aws_[A-Za-z0-9_]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_]+)?)",
    re.MULTILINE,
)
def configured_root():
    value = os.environ.get("IAC_PROVIDER_CONTRACT_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    value = os.environ.get("IAC_KG_REPLICATION_ROOT", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "full_kg"
        / "provider_kg"
    ).resolve()


def _normalize(text):
    return re.sub(r"[^a-z0-9_+.#/-]+", " ", str(text or "").lower())


def _tokenize(text):
    normalized = _normalize(text).replace("_", " ").replace("-", " ")
    return [
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token and token not in TOKEN_STOPWORDS
    ]


def _phrase_present(prompt_text, phrase):
    phrase = _normalize(phrase).strip()
    if not phrase:
        return False
    return bool(re.search(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])", prompt_text))


def _phrase_score(prompt_text, phrase, weight=1):
    phrase = _normalize(phrase).strip()
    if not phrase:
        return 0
    if _phrase_present(prompt_text, phrase):
        return weight * (8 + 2 * len(phrase.split()))
    return 0


def _load_jsonl(path):
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _item_names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def _item_type_map(items):
    result = {}
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            result[str(item["name"])] = str(item.get("type", ""))
    return result


def _resource_aliases(resource_type, record):
    aliases = set(RESOURCE_ALIASES.get(resource_type, []))
    aliases.update(EXTRA_RESOURCE_ALIASES.get(resource_type, []))
    label = resource_type.removeprefix("aws_").replace("_", " ")
    if len(label.split()) > 1 or resource_type in ALLOW_SINGLE_LABEL_TYPES:
        aliases.add(label)
    parts = label.split()
    if len(parts) >= 2:
        aliases.add(" ".join(parts[-2:]))
    if "route53" in label:
        aliases.add(label.replace("route53", "route 53"))
    description = _normalize(record.get("description", ""))
    if resource_type == "aws_route53_zone" and "hosted zone" in description:
        aliases.add("hosted zone")
    if resource_type == "aws_route53_query_log" and "query log" in description:
        aliases.add("query log")
    if "elastic beanstalk" in description:
        aliases.add("elastic beanstalk " + parts[-1])
    return sorted(alias for alias in aliases if alias)


def _contract_from_record(record):
    resource_type = record.get("resource_type", "")
    required = _item_names(record.get("required_args"))
    optional = _item_names(record.get("optional_args"))
    computed = _item_names(record.get("attributes"))
    schema_required, schema_optional, schema_computed = provider_schema.attribute_sets(
        resource_type
    )
    blocks = []
    for block in record.get("blocks", []) or []:
        if not isinstance(block, dict) or not block.get("name"):
            continue
        blocks.append(
            {
                "block": str(block.get("name")),
                "id": str(block.get("id", block.get("name"))),
                "nesting_mode": str(block.get("nesting_mode", "")),
                "cardinality": block.get("cardinality", []),
                "required_attrs_when_used": _item_names(block.get("required_args")),
                "optional_attrs_when_used": _item_names(block.get("optional_args"))[:MAX_OPTIONAL_ATTRS],
                "source": "public_provider_schema_and_official_docs",
                "syntax_rule": f"use nested block syntax: {block.get('name')} {{ ... }}",
            }
        )
    return {
        "type": resource_type,
        "kind": record.get("kind", "resource"),
        "required_attrs": sorted(set(required) | set(schema_required)),
        "useful_optional_attrs": sorted(set(optional) | set(schema_optional))[:MAX_OPTIONAL_ATTRS],
        "computed_only_attrs": sorted(set(computed) | set(schema_computed))[:MAX_COMPUTED_ATTRS],
        "nested_blocks": blocks[:MAX_BLOCKS],
        "arg_types": {
            **_item_type_map(record.get("required_args")),
            **_item_type_map(record.get("optional_args")),
        },
        "description": re.sub(r"\s+", " ", str(record.get("description", ""))).strip()[:500],
        "source_doc": record.get("source_doc", ""),
        "aliases": _resource_aliases(resource_type, record),
        "examples": record.get("examples", []) or [],
    }


def _expr_for_attr(attr, source_type, source_attr):
    if attr.endswith("ids") or attr in {
        "subnet_ids",
        "security_group_ids",
        "vpc_security_group_ids",
        "route_table_ids",
        "target_group_arns",
    }:
        return f"{attr} = [{source_type}.<name>.{source_attr}]"
    return f"{attr} = {source_type}.<name>.{source_attr}"


def _template_key(item):
    return (
        item.get("from_type", ""),
        item.get("to_type", ""),
        item.get("attr", ""),
        item.get("expr_hint", ""),
    )


def _add_template(templates, item):
    from_type = item.get("from_type", "")
    to_type = item.get("to_type", "")
    attr = item.get("attr", "")
    if from_type and from_type == to_type:
        return
    if from_type == "aws_route53_record" and to_type in {"aws_elb", "aws_lb"} and attr in {"name", "zone_id"}:
        attr = f"alias.{attr}"
        item = dict(item)
        item["attr"] = attr
        item["expr_hint"] = f"{attr} = {to_type}.<name>.{'dns_name' if attr.endswith('name') else 'zone_id'}"
    if not provider_schema.resource_type_exists(from_type):
        return
    if to_type and not provider_schema.resource_type_exists(to_type):
        return
    if attr:
        attr_root = attr.split(".", 1)[0]
        if (
            attr_root not in provider_schema.assignable_attributes(from_type)
            and attr_root not in provider_schema.nested_block_types(from_type)
        ):
            return
    key = _template_key(item)
    current = templates.get(key)
    if current is None or item.get("confidence", 0) > current.get("confidence", 0):
        templates[key] = item


def _templates_from_example(record):
    source_type = record.get("resource_type", "")
    for example in record.get("examples", []) or []:
        code = str(example.get("code", "") or "")
        if not code:
            continue
        for block in RESOURCE_BLOCK_RE.finditer(code):
            if block.group("type") != source_type:
                continue
            body = block.group("body")
            for assignment in ASSIGNMENT_WITH_REF_RE.finditer(body):
                attr = assignment.group("attr")
                expr = assignment.group("expr").strip()
                for target_type, _, target_attr in TF_REF_RE.findall(expr):
                    if target_type == source_type:
                        continue
                    if not provider_schema.resource_type_exists(target_type):
                        continue
                    yield {
                        "from_type": source_type,
                        "to_type": target_type,
                        "attr": attr,
                        "expr_hint": f"{attr} = {target_type}.<name>.{target_attr or 'id'}",
                        "required": attr in provider_schema.required_attributes(source_type),
                        "confidence": 0.98,
                        "source_kind": "official_doc_example",
                        "evidence_id": f"provider_contract_example:{source_type}.{attr}->{target_type}",
                    }


@lru_cache(maxsize=4)
def _load_contract_index(root_text):
    root = Path(root_text)
    records = _load_jsonl(root / "resources.jsonl")
    edges = _load_jsonl(root / "kg_edges.jsonl")
    contracts = {}
    templates = {}
    for record in records:
        resource_type = record.get("resource_type", "")
        if not provider_schema.resource_type_exists(resource_type):
            continue
        contracts[resource_type] = _contract_from_record(record)
    for edge in edges:
        source_type = edge.get("from", "")
        target_type = edge.get("to", "")
        attr = str(edge.get("attribute") or "")
        if not source_type or not target_type:
            continue
        if attr:
            expr = edge.get("expr_hint") or _expr_for_attr(
                attr, target_type, REFERENCE_ATTR_HINTS.get(attr, (target_type, "id"))[1]
            )
            _add_template(
                templates,
                {
                    "from_type": source_type,
                    "to_type": target_type,
                    "attr": attr,
                    "expr_hint": expr,
                    "required": attr in provider_schema.required_attributes(source_type),
                    "confidence": 0.92 if edge.get("source") == "schema_reference_hint" else 0.86,
                    "source_kind": edge.get("source", "public_provider_edge"),
                    "evidence_id": f"provider_contract_edge:{source_type}.{attr}->{target_type}",
                },
            )
        else:
            _add_template(
                templates,
                {
                    "from_type": source_type,
                    "to_type": target_type,
                    "attr": "",
                    "expr_hint": edge.get("expr_hint", ""),
                    "required": False,
                    "confidence": 0.55,
                    "source_kind": edge.get("source", "documentation_example"),
                    "evidence_id": f"provider_contract_doc_edge:{source_type}->{target_type}",
                },
            )
    for record in records:
        for item in _templates_from_example(record):
            _add_template(templates, item)
    for resource_type, contract in contracts.items():
        for attr in contract["required_attrs"] + contract["useful_optional_attrs"]:
            hint = REFERENCE_ATTR_HINTS.get(attr)
            if not hint:
                continue
            target_type, target_attr = hint
            _add_template(
                templates,
                {
                    "from_type": resource_type,
                    "to_type": target_type,
                    "attr": attr,
                    "expr_hint": _expr_for_attr(attr, target_type, target_attr),
                    "required": attr in contract["required_attrs"],
                    "confidence": 0.9 if attr in contract["required_attrs"] else 0.78,
                    "source_kind": "provider_schema_name_rule",
                    "evidence_id": f"provider_contract_name_rule:{resource_type}.{attr}->{target_type}",
                },
            )
    metadata = {}
    metadata_path = root / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            metadata = {}
    return {
        "root": str(root),
        "metadata": metadata,
        "contracts": contracts,
        "templates": list(templates.values()),
    }


def _score_resource(prompt_text, prompt_tokens, resource_type, contract):
    score = 0
    if _phrase_present(prompt_text, resource_type):
        score += 80
    label = resource_type.removeprefix("aws_").replace("_", " ")
    if len(label.split()) > 1 or resource_type in ALLOW_SINGLE_LABEL_TYPES:
        score += _phrase_score(prompt_text, label, weight=3)
    for alias in contract.get("aliases", []):
        score += _phrase_score(prompt_text, alias, weight=4)
    if resource_type == "aws_elb" and any(
        _phrase_present(prompt_text, phrase)
        for phrase in ("elastic load balancer", "classic load balancer", "elb")
    ):
        score += 40
    if resource_type == "aws_lb" and any(
        _phrase_present(prompt_text, phrase)
        for phrase in ("application load balancer", "network load balancer", "alb", "nlb")
    ):
        score += 40
    if resource_type == "aws_lb" and _phrase_present(prompt_text, "elastic load balancer"):
        score -= 25
    return score


def _canonical_reference_target(attr):
    hint = REFERENCE_ATTR_HINTS.get(str(attr or ""))
    return hint[0] if hint else ""


def _canonical_dependency_ok(attr, to_type):
    canonical = _canonical_reference_target(attr)
    return bool(canonical and canonical == to_type)


def _matched_bundles(prompt_text):
    matched = []
    for bundle in CONCEPT_BUNDLES:
        score = sum(_phrase_score(prompt_text, trigger, weight=3) for trigger in bundle["triggers"])
        if score > 0:
            matched.append((score, bundle))
    return [bundle for _, bundle in sorted(matched, key=lambda item: (-item[0], item[1]["id"]))]


def _select_resources(prompt, index):
    prompt_text = _normalize(prompt)
    prompt_tokens = set(_tokenize(prompt_text))
    scores = {}
    reasons = defaultdict(list)
    for resource_type, contract in index["contracts"].items():
        score = _score_resource(prompt_text, prompt_tokens, resource_type, contract)
        if score > 0:
            scores[resource_type] = max(scores.get(resource_type, 0), score)
            reasons[resource_type].append("prompt_alias_or_schema_doc_match")
    classic_elb = any(
        _phrase_present(prompt_text, phrase)
        for phrase in ("elastic load balancer", "classic load balancer", "elb")
    )
    modern_lb = any(
        _phrase_present(prompt_text, phrase)
        for phrase in ("application load balancer", "network load balancer", "alb", "nlb")
    )
    if classic_elb and not modern_lb and "aws_elb" in scores:
        scores.pop("aws_lb", None)
        reasons.pop("aws_lb", None)
    bundles = _matched_bundles(prompt_text)
    for bundle in bundles:
        for resource_type in bundle["resources"]:
            if resource_type not in index["contracts"]:
                continue
            scores[resource_type] = max(scores.get(resource_type, 0), 70)
            reasons[resource_type].append(f"concept_bundle:{bundle['id']}")
    selected = [
        resource_type
        for resource_type, _ in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )
    ][:MAX_RESOURCES]
    selected_set = set(selected)

    # Add public schema/doc sources for required reference arguments.
    for template in sorted(
        index["templates"],
        key=lambda item: (-int(bool(item.get("required"))), -item.get("confidence", 0), item.get("from_type", "")),
    ):
        if len(selected) >= MAX_RESOURCES:
            break
        from_type = template.get("from_type")
        to_type = template.get("to_type")
        attr = template.get("attr", "")
        if from_type not in selected_set or to_type in selected_set:
            continue
        if not attr:
            continue
        attr_tokens = set(_tokenize(attr))
        should_add = _canonical_dependency_ok(attr, to_type) and (
            bool(template.get("required")) or bool(attr_tokens & prompt_tokens)
        )
        if should_add and to_type in index["contracts"]:
            selected.append(to_type)
            selected_set.add(to_type)
            reasons[to_type].append(f"dependency_closure:{from_type}.{attr}")
    return selected, reasons, bundles, prompt_tokens


def _candidate_resource(resource_type, contract, reasons):
    return {
        "type": resource_type,
        "evidence_id": f"provider_contract_resource:{resource_type}",
        "reason": "Task-conditioned match against public Terraform provider contract aliases/schema/docs.",
        "retrieval_role": "candidate_or_dependency_closure",
        "matched_by": sorted(set(reasons.get(resource_type, [])))[:6],
        "required_attrs": contract["required_attrs"][:MAX_OPTIONAL_ATTRS],
        "useful_optional_attrs": contract["useful_optional_attrs"][:MAX_OPTIONAL_ATTRS],
        "computed_only_attrs": contract["computed_only_attrs"][:MAX_COMPUTED_ATTRS],
        "nested_blocks": [block["block"] for block in contract["nested_blocks"][:MAX_BLOCKS]],
        "source_files": [contract.get("source_doc", "")] if contract.get("source_doc") else [],
    }


def _selected_dependencies(index, selected, prompt_tokens):
    selected_set = set(selected)
    deps = []
    seen = set()
    for template in sorted(
        index["templates"],
        key=lambda item: (
            -int(bool(item.get("required"))),
            -item.get("confidence", 0),
            item.get("from_type", ""),
            item.get("attr", ""),
        ),
    ):
        if template.get("from_type") not in selected_set or template.get("to_type") not in selected_set:
            continue
        attr = str(template.get("attr", ""))
        if not attr:
            # Keep doc-only edges for IR evidence only when both endpoints are explicit.
            attr_tokens = set()
        else:
            attr_tokens = set(_tokenize(attr))
        route53_alias_template = (
            template.get("from_type") == "aws_route53_record"
            and str(attr).startswith("alias.")
            and template.get("to_type") in {"aws_elb", "aws_lb"}
        )
        if attr and not (template.get("required") or attr_tokens & prompt_tokens or route53_alias_template):
            continue
        if attr and _canonical_reference_target(attr) and not _canonical_dependency_ok(attr, template.get("to_type")):
            continue
        key = _template_key(template)
        if key in seen:
            continue
        seen.add(key)
        deps.append(
            {
                "from_type": template.get("from_type", ""),
                "to_type": template.get("to_type", ""),
                "attr": attr,
                "expr_hint": template.get("expr_hint", ""),
                "required": bool(template.get("required")),
                "confidence": round(float(template.get("confidence", 0)), 3),
                "source_kind": template.get("source_kind", ""),
                "evidence_id": template.get("evidence_id", ""),
            }
        )
        if len(deps) >= MAX_DEPENDENCIES:
            break
    return deps


def _prompt_slots(prompt):
    text = str(prompt or "")
    lower = text.lower()
    slots = defaultdict(list)

    def add(key, value):
        value = str(value).strip()
        if value and value not in slots[key]:
            slots[key].append(value)

    for value in re.findall(r'"([^"]{1,120})"', text):
        add("quoted_literals", value)
    for value in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b", text):
        add("cidr_blocks", value)
    for value in re.findall(r"\b[a-z]{2}-[a-z]+-\d[a-z]?\b", text.lower()):
        key = "availability_zones" if re.search(r"\d[a-z]$", value) else "regions"
        add(key, value)
    for value in re.findall(r"\b(?:[1-9]\d{0,4}|0)\b", text):
        number = int(value)
        if 0 <= number <= 65535:
            add("ports", value)
    for value in re.findall(r"\b(?:HTTP|HTTPS|TCP|UDP|TLS|SSL|ICMP)\b", text, flags=re.I):
        add("protocols", value.lower())
    for value in re.findall(r"\b(?:A|AAAA|CNAME|MX|NS|TXT|SRV|CAA)\b", text):
        add("route53_record_types", value)
    for value in re.findall(r"\b[a-z0-9.-]+\.[a-z]{2,}\b", text.lower()):
        add("dns_names", value)
    for value in re.findall(r"\b[A-Za-z0-9]+:[A-Za-z0-9*]+(?:[A-Za-z0-9*:/_-]*)\b", text):
        if ":" in value and not value.startswith(("http:", "https:")):
            add("iam_actions", value)
    concept_phrases = [
        "alias record",
        "block public access",
        "bucket encryption",
        "bucket policy",
        "bucket versioning",
        "elastic load balancer",
        "internet gateway",
        "lambda execution role",
        "load balancer",
        "public access block",
        "public subnet",
        "query log",
        "query logging",
        "server side encryption",
        "server-side encryption",
        "static website",
        "weighted record",
        "weighted routing",
    ]
    for phrase in concept_phrases:
        if phrase in lower:
            add("cloud_concepts", phrase)
    return {key: values[:16] for key, values in slots.items()}


def _value_bindings(prompt, selected):
    slots = _prompt_slots(prompt)
    bindings = []
    selected_set = set(selected)
    if "cidr_blocks" in slots:
        for value in slots["cidr_blocks"]:
            if "aws_vpc" in selected_set:
                bindings.append({"value": value, "target_type": "aws_vpc", "attribute": "cidr_block"})
            elif "aws_subnet" in selected_set:
                bindings.append({"value": value, "target_type": "aws_subnet", "attribute": "cidr_block"})
    for value in slots.get("availability_zones", []):
        if "aws_subnet" in selected_set:
            bindings.append({"value": value, "target_type": "aws_subnet", "attribute": "availability_zone"})
    for value in slots.get("ports", []):
        if "aws_security_group" in selected_set or "aws_security_group_rule" in selected_set:
            target_type = "aws_security_group_rule" if "aws_security_group_rule" in selected_set else "aws_security_group"
            bindings.append({"value": value, "target_type": target_type, "attribute": "from_port/to_port"})
    for value in slots.get("route53_record_types", []):
        if "aws_route53_record" in selected_set:
            bindings.append({"value": value, "target_type": "aws_route53_record", "attribute": "type"})
    for value in slots.get("dns_names", []):
        if "aws_route53_zone" in selected_set:
            bindings.append({"value": value, "target_type": "aws_route53_zone", "attribute": "name"})
    for value in slots.get("quoted_literals", []):
        lowered = value.lower()
        if "bucket" in lowered and "aws_s3_bucket" in selected_set:
            bindings.append({"value": value, "target_type": "aws_s3_bucket", "attribute": "bucket"})
        elif "role" in lowered and "aws_iam_role" in selected_set:
            bindings.append({"value": value, "target_type": "aws_iam_role", "attribute": "name"})
        else:
            bindings.append({"value": value, "target_type": "task_selected_resource", "attribute": "name_or_tags.Name"})
    return bindings[:MAX_VALUE_BINDINGS], slots


def _usage_constraints(selected):
    constraints = []
    selected_set = set(selected)
    if "aws_dynamodb_table" in selected_set:
        constraints.append(
            {
                "resource_type": "aws_dynamodb_table",
                "rule": "Define attribute blocks only for hash_key, range_key, or secondary-index keys; do not model ordinary item fields as attribute blocks.",
                "prevents": "terraform plan error: all attributes must be indexed",
                "source_kind": "official_provider_docs_usage_constraint",
            }
        )
    if "aws_route53_record" in selected_set:
        constraints.append(
            {
                "resource_type": "aws_route53_record",
                "rule": "For load-balancer alias records, use alias { name, zone_id, evaluate_target_health } and omit records/ttl.",
                "prevents": "invalid Route53 alias HCL or OPA route53_record mismatch",
                "source_kind": "official_provider_docs_usage_constraint",
            }
        )
        constraints.append(
            {
                "resource_type": "aws_route53_record",
                "rule": "Weighted records use weighted_routing_policy { weight = ... } with set_identifier; do not invent aws_route53_record_set or aws_route53_weighted_routing_policy resources.",
                "prevents": "invalid resource type",
                "source_kind": "provider_schema_negative_constraint",
            }
        )
    if "aws_db_instance" in selected_set:
        constraints.append(
            {
                "resource_type": "aws_db_instance",
                "rule": "Use db_subnet_group_name and vpc_security_group_ids for VPC placement; aws_db_instance has no top-level vpc_id argument.",
                "prevents": "unsupported argument vpc_id",
                "source_kind": "provider_schema_usage_constraint",
            }
        )
    if "aws_iam_role" in selected_set:
        constraints.append(
            {
                "resource_type": "aws_iam_role",
                "rule": "assume_role_policy is required; use a JSON policy document with the AWS service principal named by the prompt.",
                "prevents": "missing required argument assume_role_policy",
                "source_kind": "provider_schema_usage_constraint",
            }
        )
    return constraints


def _negative_constraints(prompt):
    prompt_text = _normalize(prompt)
    constraints = []
    for invalid_type, replacement in INVALID_RESOURCE_ALIASES.items():
        if _phrase_present(prompt_text, invalid_type) or invalid_type in prompt_text:
            constraints.append(
                {
                    "invalid_resource_type": invalid_type,
                    "use_instead": replacement,
                    "source_kind": "public_provider_schema_absence",
                }
            )
    constraints.extend(
        {
            "invalid_resource_type": invalid_type,
            "use_instead": replacement,
            "source_kind": "public_provider_schema_absence",
        }
        for invalid_type, replacement in INVALID_RESOURCE_ALIASES.items()
        if invalid_type.startswith("aws_route53_")
    )
    return constraints[:8]


def retrieve_public_provider_contract_evidence(prompt):
    root = configured_root()
    index = _load_contract_index(str(root))
    versioning.assert_version_alignment(
        index.get("metadata", {}).get("provider_version", "")
    )
    selected, reasons, bundles, prompt_tokens = _select_resources(prompt, index)
    deps = _selected_dependencies(index, selected, prompt_tokens)
    value_bindings, slots = _value_bindings(prompt, selected)

    candidate_resources = [
        _candidate_resource(resource_type, index["contracts"][resource_type], reasons)
        for resource_type in selected
        if resource_type in index["contracts"]
    ]
    resource_contracts = {
        resource_type: {
            "required_args": index["contracts"][resource_type]["required_attrs"][:MAX_OPTIONAL_ATTRS],
            "optional_args_relevant_to_prompt": index["contracts"][resource_type]["useful_optional_attrs"][:MAX_OPTIONAL_ATTRS],
            "computed_attrs": index["contracts"][resource_type]["computed_only_attrs"][:MAX_COMPUTED_ATTRS],
            "nested_blocks": index["contracts"][resource_type]["nested_blocks"][:MAX_BLOCKS],
            "arg_types": index["contracts"][resource_type]["arg_types"],
        }
        for resource_type in selected
        if resource_type in index["contracts"]
    }
    evidence = {
        "evidence_kind": "task_conditioned_provider_contract",
        "contract_kind": "public_provider_contract_graph",
        "source_policy": (
            "Built from the full public Terraform AWS provider schema and official provider docs/examples "
            "under the configured public KG root. Runtime retrieval uses only the visible prompt. "
            "No IaC-Eval Resource, Intent, Rego intent, Reference output, validation result, plan result, "
            "OPA result, generated HCL, or repair trace is used."
        ),
        "retrieval_method": {
            "entity_linking": "deterministic resource alias/schema-doc matching plus weak lexical overlap",
            "dependency_closure": "public provider schema name rules and official-doc example reference templates",
            "injection_granularity": {
                "ir": "resource candidates, dependency closure, required resource hints, prompt slots",
                "hcl": "argument contracts, nested block contracts, reference templates, usage constraints, value bindings",
            },
            "package_root": index["root"],
            "provider_version": index.get("metadata", {}).get("provider_version", ""),
        },
        "candidate_resources": candidate_resources,
        "required_resource_hints": [
            {
                "resource_type": resource_type,
                "confidence": "high",
                "reason": "Task concept bundle requires this public Terraform resource type.",
                "evidence_id": f"provider_contract_bundle:{bundle['id']}:{resource_type}",
            }
            for bundle in bundles
            for resource_type in bundle["resources"]
            if resource_type in selected
        ],
        "dependency_hints": deps,
        "nested_block_hints": [
            {
                "resource_type": resource_type,
                "block": block["block"],
                "required_attrs": block.get("required_attrs_when_used", []),
                "known_attrs": block.get("required_attrs_when_used", []) + block.get("optional_attrs_when_used", []),
                "syntax_rule": block.get("syntax_rule", ""),
                "evidence_id": f"provider_contract_block:{resource_type}.{block['block']}",
            }
            for resource_type in selected
            for block in index["contracts"].get(resource_type, {}).get("nested_blocks", [])[:4]
        ][:MAX_BLOCKS],
        "schema_facts": [
            {
                "type": item["type"],
                "required_attrs": item["required_attrs"],
                "useful_optional_attrs": item["useful_optional_attrs"],
                "computed_only_attrs": item["computed_only_attrs"],
            }
            for item in candidate_resources
        ],
        "matched_patterns": [
            {"id": bundle["id"], "resources": [r for r in bundle["resources"] if r in selected]}
            for bundle in bundles
        ],
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
        "provider_contract": {
            "resource_contracts": resource_contracts,
            "dependency_templates": deps,
            "prompt_semantic_slots": slots,
            "value_bindings": value_bindings,
            "usage_constraints": _usage_constraints(selected),
            "negative_constraints": _negative_constraints(prompt),
        },
    }
    return json.dumps(evidence, indent=2, sort_keys=True)
