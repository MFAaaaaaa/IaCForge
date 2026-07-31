"""Task-conditioned public Terraform provider contract retriever.

This retriever is intentionally not a paper-KG replica. It derives typed
resource contracts and dependency templates from the self-built public AWS
provider docs/schema package plus the bundled provider schema. Runtime
selection uses only the visible prompt.
"""

import hashlib
import json
import math
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
MAX_EXAMPLE_PATTERNS = int(os.environ.get("IAC_CONTRACT_MAX_EXAMPLE_PATTERNS", "6"))
MAX_EXAMPLE_PATTERNS_PER_SOURCE = int(os.environ.get("IAC_CONTRACT_MAX_EXAMPLE_PATTERNS_PER_SOURCE", "2"))
MAX_PATTERN_ATTRS = int(os.environ.get("IAC_CONTRACT_MAX_PATTERN_ATTRS", "10"))
MAX_PATTERN_LITERALS = int(os.environ.get("IAC_CONTRACT_MAX_PATTERN_LITERALS", "8"))
MIN_RESOURCES = int(os.environ.get("IAC_CONTRACT_MIN_RESOURCES", "3"))
RESOURCE_SCORE_THRESHOLD = float(
    os.environ.get("IAC_CONTRACT_SCORE_THRESHOLD", "8")
)
RESOURCE_SCORE_GAP = float(os.environ.get("IAC_CONTRACT_SCORE_GAP", "24"))
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
SIMPLE_ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<attr>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<expr>[^\n#]+)",
    re.MULTILINE,
)
NESTED_BLOCK_START_RE = re.compile(r"^\s*(?P<block>[A-Za-z_][A-Za-z0-9_]*)\s*\{", re.MULTILINE)
IAM_ACTION_RE = re.compile(r"\b[A-Za-z0-9]+:[A-Za-z0-9*]+(?:[A-Za-z0-9*:/_-]*)\b")
SAFE_LITERAL_ATTRS = {
    "acl",
    "action",
    "actions",
    "effect",
    "engine",
    "event",
    "events",
    "filter_prefix",
    "filter_suffix",
    "identifier",
    "instance_class",
    "principal",
    "principals",
    "protocol",
    "routing_policy",
    "sse_algorithm",
    "status",
    "storage_class",
    "target_prefix",
    "type",
}
SKIP_LITERAL_ATTRS = {
    "arn",
    "bucket",
    "description",
    "domain",
    "domain_name",
    "id",
    "name",
    "name_prefix",
    "policy_name",
    "tags",
    "tags_all",
}
PATTERN_STOPWORDS = TOKEN_STOPWORDS | {
    "basic",
    "example",
    "usage",
    "terraform",
    "hashicorp",
    "provider",
    "latest",
    "docs",
}
WEAK_PATTERN_TOKENS = PATTERN_STOPWORDS | {
    "access",
    "arn",
    "bucket",
    "cloudwatch",
    "configuration",
    "create",
    "default",
    "database",
    "databases",
    "group",
    "id",
    "endpoint",
    "endpoints",
    "instance",
    "instances",
    "log",
    "logs",
    "main",
    "policy",
    "route",
    "role",
    "rds",
    "subnet",
    "target",
}
BROAD_RESOURCE_ALIASES = {
    "access policy",
    "account policy",
    "domain name",
    "resource policy",
    "role policy",
    "subnet group",
}

RULE_SOURCE = "author_defined_public_domain_knowledge"


def retrieval_rule_catalog():
    """Return every handwritten retrieval rule as an auditable data record."""

    rules = []
    merged_aliases = defaultdict(set)
    for mapping in (RESOURCE_ALIASES, EXTRA_RESOURCE_ALIASES):
        for resource_type, terms in mapping.items():
            merged_aliases[resource_type].update(terms)
    for resource_type, terms in sorted(merged_aliases.items()):
        rules.append(
            {
                "rule_id": f"alias:{resource_type}",
                "kind": "resource_alias",
                "terms": sorted(terms),
                "candidate_resources": [resource_type],
                "source": RULE_SOURCE,
            }
        )
    for bundle in CONCEPT_BUNDLES:
        rules.append(
            {
                "rule_id": f"concept:{bundle['id']}",
                "kind": "concept_bundle",
                "terms": list(bundle.get("triggers", [])),
                "candidate_resources": list(bundle.get("resources", [])),
                "source": RULE_SOURCE,
            }
        )
    for attr, (resource_type, target_attr) in sorted(REFERENCE_ATTR_HINTS.items()):
        rules.append(
            {
                "rule_id": f"reference_hint:{attr}",
                "kind": "schema_name_hint",
                "source_path": attr,
                "target_type": resource_type,
                "target_path": target_attr,
                "source": RULE_SOURCE,
                "confidence": 0.55,
            }
        )
    return rules


def retrieval_rules_sha256():
    payload = json.dumps(
        retrieval_rule_catalog(), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        / "leakfree_multigranular_kg"
        / "terraform_aws_5.90.0_public_kg"
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


def _add_unique(items, value, limit=None):
    if value and value not in items and (limit is None or len(items) < limit):
        items.append(value)


def _iter_resource_blocks(code):
    text = str(code or "")
    start_re = re.compile(r'resource\s+"(?P<type>aws_[A-Za-z0-9_]+)"\s+"(?P<label>[^"]+)"\s*\{')
    for match in start_re.finditer(text):
        resource_type = match.group("type")
        label = match.group("label")
        start = match.end()
        depth = 1
        i = start
        in_string = False
        escape = False
        while i < len(text):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield resource_type, label, text[start:i]
                        break
            i += 1


def _safe_literal_value(expr):
    expr = str(expr or "").strip().rstrip(",")
    if not expr:
        return ""
    if re.fullmatch(r"(true|false)", expr, flags=re.I):
        return expr.lower()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", expr):
        return expr
    if re.fullmatch(r'"[^"\n]{1,120}"', expr):
        return expr
    if re.fullmatch(r"\[[^\]\n]{1,240}\]", expr) and '"' in expr:
        return re.sub(r"\s+", " ", expr)
    return ""


def _generalize_ref_expr(expr):
    return re.sub(
        r"\b(aws_[A-Za-z0-9_]+)\.[A-Za-z0-9_-]+\.([A-Za-z0-9_]+)",
        r"\1.<name>.\2",
        str(expr or "").strip(),
    )


def _pattern_tokens_for_example(record, example, resource_types, attrs, nested_blocks):
    pieces = [
        record.get("resource_type", ""),
        record.get("source_doc", ""),
        record.get("description", ""),
        example.get("name", ""),
    ]
    pieces.extend(resource_types)
    pieces.extend(resource_type.removeprefix("aws_").replace("_", " ") for resource_type in resource_types)
    pieces.extend(attrs)
    pieces.extend(nested_blocks)
    tokens = []
    for piece in pieces:
        for token in _tokenize(piece):
            if token not in PATTERN_STOPWORDS:
                _add_unique(tokens, token, limit=80)
    return tokens


def _feature_tokens_for_example(attrs, nested_blocks, iam_actions):
    tokens = []
    for piece in list(attrs or []) + list(nested_blocks or []) + list(iam_actions or []):
        for token in _tokenize(piece):
            if token not in PATTERN_STOPWORDS:
                _add_unique(tokens, token, limit=80)
    return tokens


def _example_patterns_from_record(record):
    source_type = record.get("resource_type", "")
    if not source_type or not provider_schema.resource_type_exists(source_type):
        return []
    patterns = []
    for example in record.get("examples", []) or []:
        code = str(example.get("code", "") or "")
        if not code:
            continue
        resource_types = []
        per_resource = defaultdict(lambda: {"attrs": [], "nested_blocks": [], "literal_hints": [], "reference_hints": []})
        all_attrs = []
        all_nested = []
        all_actions = []
        for resource_type, _, body in _iter_resource_blocks(code):
            if not provider_schema.resource_type_exists(resource_type):
                continue
            _add_unique(resource_types, resource_type)
            assignable = provider_schema.assignable_attributes(resource_type)
            nested_block_types = provider_schema.nested_block_types(resource_type)
            for assignment in SIMPLE_ASSIGNMENT_RE.finditer(body):
                attr = assignment.group("attr")
                expr = assignment.group("expr").strip()
                if attr not in assignable:
                    continue
                _add_unique(per_resource[resource_type]["attrs"], attr, MAX_PATTERN_ATTRS)
                _add_unique(all_attrs, attr, MAX_PATTERN_ATTRS * 2)
                if "aws_" in expr:
                    _add_unique(
                        per_resource[resource_type]["reference_hints"],
                        f"{attr} = {_generalize_ref_expr(expr)}",
                        MAX_PATTERN_ATTRS,
                    )
                    continue
                literal = _safe_literal_value(expr)
                if not literal or attr in SKIP_LITERAL_ATTRS:
                    continue
                if attr in SAFE_LITERAL_ATTRS or literal in {"true", "false"}:
                    _add_unique(
                        per_resource[resource_type]["literal_hints"],
                        f"{attr} = {literal}",
                        MAX_PATTERN_LITERALS,
                    )
            for block_match in NESTED_BLOCK_START_RE.finditer(body):
                block_name = block_match.group("block")
                if block_name in nested_block_types:
                    _add_unique(per_resource[resource_type]["nested_blocks"], block_name, MAX_PATTERN_ATTRS)
                    _add_unique(all_nested, block_name, MAX_PATTERN_ATTRS * 2)
        for action in IAM_ACTION_RE.findall(code):
            if not action.startswith(("http:", "https:")):
                _add_unique(all_actions, action, MAX_PATTERN_LITERALS)
        if not resource_types:
            continue
        tokens = _pattern_tokens_for_example(record, example, resource_types, all_attrs, all_nested)
        pattern_id = f"public_example_pattern:{source_type}:{example.get('index', len(patterns))}"
        patterns.append(
            {
                "id": pattern_id,
                "source_resource_type": source_type,
                "name": str(example.get("name", "") or "official provider example"),
                "resource_types": resource_types[:MAX_RESOURCES],
                "tokens": tokens,
                "feature_tokens": _feature_tokens_for_example(all_attrs, all_nested, all_actions),
                "per_resource": dict(per_resource),
                "iam_actions": all_actions,
                "source_doc": record.get("source_doc", ""),
            }
        )
    return patterns


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
        tail_alias = " ".join(parts[-2:])
        if tail_alias not in BROAD_RESOURCE_ALIASES:
            aliases.add(tail_alias)
    if "route53" in label:
        aliases.add(label.replace("route53", "route 53"))
    description = _normalize(record.get("description", ""))
    if resource_type == "aws_route53_zone" and "hosted zone" in description:
        aliases.add("hosted zone")
    if resource_type == "aws_route53_query_log" and "query log" in description:
        aliases.add("query log")
    if "elastic beanstalk" in description:
        aliases.add("elastic beanstalk " + parts[-1])
    return sorted(alias for alias in aliases if alias and alias not in BROAD_RESOURCE_ALIASES)


def _contract_from_record(record):
    resource_type = record.get("resource_type", "")
    kind = str(record.get("kind", "resource")).replace(" ", "_")
    if kind not in {"resource", "data_source"}:
        kind = "resource"
    required = _item_names(record.get("required_args"))
    optional = _item_names(record.get("optional_args"))
    computed = _item_names(record.get("attributes"))
    schema_required, schema_optional, schema_computed = provider_schema.attribute_sets(
        resource_type, kind
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
        "entity_id": (
            f"aws@{versioning.AWS_PROVIDER_VERSION}::{kind}::{resource_type}"
        ),
        "type": resource_type,
        "kind": kind,
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
    item = dict(item)
    item.setdefault("support_count", 1)
    item.setdefault("provider_version", versioning.AWS_PROVIDER_VERSION)
    item.setdefault("relation", "REQUIRES_VALUE_OF_TYPE")
    item.setdefault("provenance", item.get("source_kind", ""))
    item.setdefault(
        "edge_id",
        f"ref:{from_type}.{attr}->{to_type}.{item.get('target_path', 'id')}",
    )
    if current is None:
        templates[key] = item
    else:
        combined = dict(current)
        combined["support_count"] = int(current.get("support_count", 1)) + int(
            item.get("support_count", 1)
        )
        if item.get("confidence", 0) > current.get("confidence", 0):
            combined.update(item)
            combined["support_count"] = int(current.get("support_count", 1)) + int(
                item.get("support_count", 1)
            )
        templates[key] = combined


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
                        "provenance": "official_doc_example",
                        "source_document": record.get("source_doc", ""),
                        "example_title": example.get("name", ""),
                        "target_path": target_attr or "id",
                        "relation": "REQUIRES_VALUE_OF_TYPE",
                        "evidence_id": f"provider_contract_example:{source_type}.{attr}->{target_type}",
                    }


@lru_cache(maxsize=4)
def _load_contract_index(root_text):
    root = Path(root_text)
    records = _load_jsonl(root / "resources.jsonl")
    edges = _load_jsonl(root / "kg_edges.jsonl")
    contracts = {}
    templates = {}
    example_patterns = []
    for record in records:
        resource_type = record.get("resource_type", "")
        kind = str(record.get("kind", "resource")).replace(" ", "_")
        if kind not in {"resource", "data_source"}:
            kind = "resource"
        if not provider_schema.type_exists(resource_type, kind):
            continue
        contracts[resource_type] = _contract_from_record(record)
        example_patterns.extend(_example_patterns_from_record(record))
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
                    "confidence": 0.55
                    if edge.get("source") == "schema_reference_hint"
                    else 0.86,
                    "source_kind": edge.get("source", "public_provider_edge"),
                    "provenance": edge.get("source", "public_provider_edge"),
                    "source_document": edge.get("source_document")
                    or edge.get("source_doc", ""),
                    "target_path": edge.get("target_attribute")
                    or REFERENCE_ATTR_HINTS.get(attr, (target_type, "id"))[1],
                    "relation": edge.get("relation") or "REQUIRES_VALUE_OF_TYPE",
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
                    "confidence": 0.55,
                    "source_kind": "provider_schema_name_rule",
                    "provenance": "schema_name_hint",
                    "target_path": target_attr,
                    "relation": "REQUIRES_VALUE_OF_TYPE",
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
    dense_vectors = {}
    dense_path = root / "resource_dense_index.json"
    if dense_path.exists():
        try:
            dense_payload = json.loads(dense_path.read_text(encoding="utf-8"))
            dense_vectors = {
                str(item["type"]): list(item["vector"])
                for item in dense_payload.get("resources", [])
                if isinstance(item, dict)
                and item.get("type")
                and isinstance(item.get("vector"), list)
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            dense_vectors = {}
    return {
        "root": str(root),
        "metadata": metadata,
        "contracts": contracts,
        "templates": list(templates.values()),
        "example_patterns": example_patterns,
        "resource_document_tokens": {
            resource_type: _tokenize(_resource_document(resource_type, contract))
            for resource_type, contract in contracts.items()
        },
        "dense_vectors": dense_vectors,
    }


def public_provider_contract_available():
    root = configured_root()
    return (root / "resources.jsonl").exists() and bool(
        _load_contract_index(str(root)).get("contracts")
    )


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


def _resource_document(resource_type, contract):
    return " ".join(
        [
            resource_type.replace("_", " "),
            *contract.get("aliases", []),
            str(contract.get("description", "")),
        ]
    )


def _bm25_scores(prompt_tokens, index):
    """Small deterministic BM25 implementation over resource-level documents."""

    query = list(prompt_tokens)
    documents = index.get("resource_document_tokens", {})
    if not query or not documents:
        return {}
    document_count = len(documents)
    average_length = (
        sum(len(tokens) for tokens in documents.values()) / document_count
    )
    document_frequency = defaultdict(int)
    for tokens in documents.values():
        for token in set(tokens):
            document_frequency[token] += 1
    scores = {}
    k1 = 1.5
    b = 0.75
    for resource_type, tokens in documents.items():
        frequencies = defaultdict(int)
        for token in tokens:
            frequencies[token] += 1
        score = 0.0
        for token in query:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            df = document_frequency[token]
            inverse_document_frequency = math.log(
                1 + (document_count - df + 0.5) / (df + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * len(tokens) / max(average_length, 1)
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1) / denominator
            )
        if score:
            scores[resource_type] = score
    return scores


def _cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _dense_scores(prompt, index):
    """Optional semantic retrieval against a prebuilt public resource index.

    Dense retrieval is enabled only when ``IAC_DENSE_RETRIEVAL=1`` and
    ``resource_dense_index.json`` exists under the KG root.  The index contains
    only type, alias, description and short public-purpose text.
    """

    enabled = os.environ.get("IAC_DENSE_RETRIEVAL", "0").lower() in {
        "1",
        "true",
        "yes",
    }
    vectors = index.get("dense_vectors", {})
    if not enabled or not vectors:
        return {}
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ.get("IAC_EMBEDDING_API_KEY", "EMPTY"),
            base_url=os.environ.get(
                "IAC_EMBEDDING_BASE_URL",
                os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
            ),
        )
        model = os.environ.get("IAC_EMBEDDING_MODEL", "text-embedding-3-small")
        vector = client.embeddings.create(model=model, input=[str(prompt)]).data[0].embedding
    except Exception:
        if os.environ.get("IAC_DENSE_REQUIRED", "0") == "1":
            raise
        return {}
    return {
        resource_type: _cosine(vector, candidate)
        for resource_type, candidate in vectors.items()
    }


def _dynamic_resource_selection(scores):
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranked:
        return []
    selected = []
    for index, (resource_type, score) in enumerate(ranked[:MAX_RESOURCES]):
        if index >= MIN_RESOURCES and score < RESOURCE_SCORE_THRESHOLD:
            break
        if (
            index >= MIN_RESOURCES
            and ranked[index - 1][1] - score > RESOURCE_SCORE_GAP
        ):
            break
        selected.append(resource_type)
    return selected


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


def _source_pattern_match(prompt_text, prompt_tokens, pattern, index):
    source_type = pattern.get("source_resource_type", "")
    contract = index.get("contracts", {}).get(source_type, {})
    if not source_type or not contract:
        return 0, ""
    if (
        source_type == "aws_lb"
        and _phrase_present(prompt_text, "elastic load balancer")
        and not any(
            _phrase_present(prompt_text, phrase)
            for phrase in ("application load balancer", "network load balancer", "alb", "nlb")
        )
    ):
        return 0, ""
    score = 0
    for phrase in [source_type, source_type.removeprefix("aws_").replace("_", " ")] + list(contract.get("aliases", [])):
        phrase_tokens = _tokenize(phrase)
        if (
            len(phrase_tokens) == 1
            and source_type not in ALLOW_SINGLE_LABEL_TYPES
            and phrase != source_type
        ):
            continue
        phrase_score = _phrase_score(prompt_text, phrase, weight=5)
        if phrase_score:
            score += phrase_score + 24
    if score > 0:
        return score, "source_resource_alias_match"

    label_tokens = {
        token
        for token in _tokenize(source_type.removeprefix("aws_").replace("_", " "))
        if token not in PATTERN_STOPWORDS
    }
    source_overlap = prompt_tokens.intersection(label_tokens)
    core_overlap = source_overlap.intersection(label_tokens - WEAK_PATTERN_TOKENS)
    if len(source_overlap) >= 2 and (
        len(source_overlap) / max(1, len(label_tokens)) >= 0.67
    ) and core_overlap:
        return 22 + 3 * len(source_overlap), "source_resource_label_token_match"
    feature_overlap = {
        token
        for token in prompt_tokens.intersection(pattern.get("feature_tokens", []))
        if token not in WEAK_PATTERN_TOKENS and token not in label_tokens and len(token) > 1
    }
    core_source_overlap = source_overlap.intersection(label_tokens - WEAK_PATTERN_TOKENS)
    route53_service_overlap = "route53" in label_tokens and {"route", "53"}.issubset(prompt_tokens)
    if (core_source_overlap or route53_service_overlap) and len(feature_overlap) >= 2:
        return 18 + 3 * len(feature_overlap), "source_service_plus_example_feature_match"
    return 0, ""


def _example_pattern_score(prompt_text, prompt_tokens, pattern, seed_types, index):
    source_score, source_reason = _source_pattern_match(prompt_text, prompt_tokens, pattern, index)
    if source_score <= 0:
        return 0, ""
    seed_set = set(seed_types)
    seed_overlap = seed_set.intersection(pattern.get("resource_types", []))
    source_type = pattern.get("source_resource_type", "")
    non_source_seed_overlap = seed_overlap - {source_type}
    token_overlap = {
        token
        for token in prompt_tokens.intersection(pattern.get("tokens", []))
        if token not in WEAK_PATTERN_TOKENS and len(token) > 1
    }
    if source_type in seed_set and len(seed_set) > 1 and not non_source_seed_overlap:
        return 0, ""
    score = source_score
    if source_type in seed_overlap:
        score += 20
    elif seed_overlap:
        score += 4 * len(seed_overlap)
    if len(token_overlap) >= 2:
        score += min(30, 4 * len(token_overlap))
    name = str(pattern.get("name", ""))
    if name and name.lower() not in {"basic usage", "example"}:
        score += _phrase_score(prompt_text, name, weight=1)
    if score < 20:
        return 0, ""
    return score, source_reason


def _matched_example_patterns(prompt_text, prompt_tokens, index, seed_types):
    scored = []
    for pattern in index.get("example_patterns", []):
        score, reason = _example_pattern_score(prompt_text, prompt_tokens, pattern, seed_types, index)
        if score > 0:
            item = dict(pattern)
            item["match_score"] = score
            item["match_reason"] = reason
            scored.append((score, item))
    result = []
    per_source = defaultdict(int)
    for _, pattern in sorted(scored, key=lambda item: (-item[0], item[1].get("id", ""))):
        source_type = pattern.get("source_resource_type", "")
        source_limit = (
            1
            if len(set(pattern.get("resource_types", []))) <= 1
            else MAX_EXAMPLE_PATTERNS_PER_SOURCE
        )
        if per_source[source_type] >= source_limit:
            continue
        result.append(pattern)
        per_source[source_type] += 1
        if len(result) >= MAX_EXAMPLE_PATTERNS:
            break
    return result


def _select_resources(prompt, index, use_static_bundles=True, use_example_patterns=False):
    prompt_text = _normalize(prompt)
    prompt_tokens = set(_tokenize(prompt_text))
    retrieval_mode = os.environ.get("IAC_RETRIEVAL_MODE", "hybrid_graph").lower()
    scores = {}
    reasons = defaultdict(list)
    for resource_type, contract in index["contracts"].items():
        score = _score_resource(prompt_text, prompt_tokens, resource_type, contract)
        if score > 0:
            scores[resource_type] = max(scores.get(resource_type, 0), score)
            reasons[resource_type].append("exact_or_alias_match")
    if retrieval_mode in {"lexical", "hybrid", "hybrid_graph"}:
        lexical = sorted(
            _bm25_scores(prompt_tokens, index).items(),
            key=lambda item: (-item[1], item[0]),
        )[:30]
        for resource_type, score in lexical:
            scaled = min(50.0, score * 8.0)
            if scaled < RESOURCE_SCORE_THRESHOLD:
                continue
            scores[resource_type] = max(scores.get(resource_type, 0), scaled)
            reasons[resource_type].append("bm25_top30")
    if retrieval_mode in {"dense", "hybrid", "hybrid_graph"}:
        dense = sorted(
            _dense_scores(prompt, index).items(),
            key=lambda item: (-item[1], item[0]),
        )[:30]
        for resource_type, similarity in dense:
            if similarity < float(os.environ.get("IAC_DENSE_THRESHOLD", "0.2")):
                continue
            scores[resource_type] = max(
                scores.get(resource_type, 0), similarity * 50.0
            )
            reasons[resource_type].append("dense_top30")
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
    example_patterns = []
    if use_example_patterns:
        example_patterns = _matched_example_patterns(prompt_text, prompt_tokens, index, scores.keys())
        for pattern in example_patterns:
            source_type = pattern.get("source_resource_type", "")
            for resource_type in pattern.get("resource_types", []):
                if resource_type not in index["contracts"]:
                    continue
                if resource_type not in scores and resource_type != source_type:
                    continue
                pattern_score = 66 if resource_type == pattern.get("source_resource_type") else 58
                scores[resource_type] = max(scores.get(resource_type, 0), pattern_score)
                reasons[resource_type].append(f"public_example_pattern:{pattern.get('id', '')}")
    bundles = _matched_bundles(prompt_text) if use_static_bundles else []
    for bundle in bundles:
        for resource_type in bundle["resources"]:
            if resource_type not in index["contracts"]:
                continue
            scores[resource_type] = max(scores.get(resource_type, 0), 70)
            reasons[resource_type].append(f"concept_bundle:{bundle['id']}")
    if retrieval_mode == "hybrid_graph":
        for template in index.get("templates", []):
            source_type = template.get("from_type", "")
            target_type = template.get("to_type", "")
            confidence = float(template.get("confidence", 0))
            if source_type not in scores or target_type not in scores:
                continue
            if confidence < 0.8:
                continue
            bonus = min(12.0, confidence * 10.0)
            scores[source_type] += bonus
            scores[target_type] += bonus
            edge_id = template.get("edge_id") or template.get("evidence_id", "")
            reasons[source_type].append(f"kg_rerank:{edge_id}")
            reasons[target_type].append(f"kg_rerank:{edge_id}")
    selected = _dynamic_resource_selection(scores)
    selected_set = set(selected)

    # Conditional dependency expansion: a value requirement is not automatically
    # a resource-creation requirement.  Existing/external cues explicitly block
    # managed-resource closure.
    external_value_cue = any(
        phrase in prompt_text
        for phrase in (
            "existing ",
            "already existing",
            "provided id",
            "given id",
            "use vpc-",
            "use subnet-",
        )
    )
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
        target_label = str(to_type).removeprefix("aws_").replace("_", " ")
        explicit_target = _phrase_present(prompt_text, target_label)
        should_add = (
            not external_value_cue
            and _canonical_dependency_ok(attr, to_type)
            and (
                template.get("relation") == "MUST_CREATE_WITH"
                or explicit_target
            )
        )
        if should_add and to_type in index["contracts"]:
            selected.append(to_type)
            selected_set.add(to_type)
            reasons[to_type].append(f"dependency_closure:{from_type}.{attr}")
            scores[to_type] = max(scores.get(to_type, 0), 40)
    return selected, reasons, bundles, prompt_tokens, example_patterns, scores


def _candidate_resource(resource_type, contract, reasons, score=0):
    return {
        "type": resource_type,
        "entity_id": contract.get("entity_id", ""),
        "evidence_id": f"provider_contract_resource:{resource_type}",
        "reason": "Task-conditioned match against public Terraform provider contract aliases/schema/docs.",
        "retrieval_role": "candidate_or_dependency_closure",
        "matched_by": sorted(set(reasons.get(resource_type, [])))[:6],
        "score": round(float(score), 4),
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
                "provenance": template.get("provenance")
                or template.get("source_kind", ""),
                "source_document": template.get("source_document", ""),
                "example_title": template.get("example_title", ""),
                "support_count": int(template.get("support_count", 1)),
                "provider_version": template.get(
                    "provider_version", versioning.AWS_PROVIDER_VERSION
                ),
                "relation": template.get("relation", "REQUIRES_VALUE_OF_TYPE"),
                "source_path": attr,
                "target_path": template.get("target_path", "id"),
                "edge_id": template.get("edge_id", ""),
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


def _example_semantic_obligations(prompt, selected, example_patterns):
    prompt_text = _normalize(prompt)
    selected_set = set(selected)
    obligations = []
    seen = set()
    for pattern in example_patterns or []:
        pattern_types = set(pattern.get("resource_types", []))
        if not pattern_types.intersection(selected_set):
            continue
        for resource_type in pattern.get("resource_types", []):
            if resource_type not in selected_set:
                continue
            resource_pattern = pattern.get("per_resource", {}).get(resource_type, {})
            attrs = list(resource_pattern.get("attrs", []))
            nested = list(resource_pattern.get("nested_blocks", []))
            literals = list(resource_pattern.get("literal_hints", []))
            refs = list(resource_pattern.get("reference_hints", []))
            actions = list(pattern.get("iam_actions", []))
            if not (attrs or nested or literals or refs or actions):
                continue
            key = (pattern.get("id", ""), resource_type)
            if key in seen:
                continue
            seen.add(key)
            details = []
            if refs:
                details.append("reference templates: " + "; ".join(refs[:4]))
            if literals:
                details.append("literal/default hints: " + "; ".join(literals[:4]))
            if nested:
                details.append("nested blocks: " + ", ".join(nested[:4]))
            if actions and ("iam" in resource_type or "policy" in resource_type or "cloudwatch_log_resource_policy" in resource_type):
                details.append("policy actions visible in the public example: " + ", ".join(actions[:6]))
            if not details:
                details.append("follow the public provider example's resource wiring and provider-valid attributes")
            obligations.append(
                {
                    "resource_type": resource_type,
                    "action": "apply_public_example_pattern",
                    "requirement": (
                        f"Apply the official provider example pattern '{pattern.get('name', 'example')}' "
                        f"for {resource_type}: " + " | ".join(details)
                        + ". Replace example-specific names/domains with visible prompt literals or realistic offline-valid defaults."
                    ),
                    "attributes_or_blocks": (attrs + nested)[:MAX_PATTERN_ATTRS],
                    "reference_templates": refs[:MAX_PATTERN_ATTRS],
                    "literal_hints": literals[:MAX_PATTERN_LITERALS],
                    "source_kind": "official_provider_example_pattern",
                    "evidence_id": pattern.get("id", ""),
                    "when": "when the resource is generated and the visible prompt matches this public example pattern",
                }
            )
    return obligations[:MAX_EXAMPLE_PATTERNS * 3]


def _semantic_obligations(prompt, selected, deps, slots, example_patterns=None, include_static=True):
    prompt_text = _normalize(prompt)
    selected_set = set(selected)
    deps = deps or []
    obligations = _example_semantic_obligations(prompt, selected, example_patterns)
    if not include_static:
        return obligations[:20]

    def add(resource_type, action, requirement, attrs=None, evidence_id="", when="when the resource is generated"):
        if resource_type not in selected_set:
            return
        item = {
            "resource_type": resource_type,
            "action": action,
            "requirement": requirement,
            "when": when,
            "source_kind": "public_provider_semantic_contract",
            "evidence_id": evidence_id or f"semantic_contract:{resource_type}:{action}",
        }
        if attrs:
            item["attributes_or_blocks"] = attrs
        obligations.append(item)

    def has_any(*phrases):
        return any(_phrase_present(prompt_text, phrase) for phrase in phrases)

    if has_any("bucket versioning", "versioning enabled", "enable versioning"):
        add(
            "aws_s3_bucket_versioning",
            "enable_s3_versioning",
            "Emit versioning_configuration { status = \"Enabled\" } for the bucket versioning resource.",
            ["versioning_configuration.status"],
            "semantic_contract:s3:versioning_enabled",
        )
    if has_any("server side encryption", "server-side encryption", "bucket encryption", "encrypted bucket", "sse"):
        algorithm = "aws:kms" if has_any("kms", "kms key") else "AES256"
        add(
            "aws_s3_bucket_server_side_encryption_configuration",
            "enable_s3_encryption",
            f"Emit rule {{ apply_server_side_encryption_by_default {{ sse_algorithm = \"{algorithm}\" }} }}.",
            ["rule.apply_server_side_encryption_by_default.sse_algorithm"],
            "semantic_contract:s3:server_side_encryption",
        )
    if has_any("block public access", "public access block", "prevent public access"):
        add(
            "aws_s3_bucket_public_access_block",
            "block_s3_public_access",
            "Set block_public_acls, ignore_public_acls, block_public_policy, and restrict_public_buckets to true.",
            [
                "block_public_acls",
                "ignore_public_acls",
                "block_public_policy",
                "restrict_public_buckets",
            ],
            "semantic_contract:s3:public_access_block",
        )
    if has_any("static website", "website hosting", "bucket website"):
        add(
            "aws_s3_bucket_website_configuration",
            "configure_s3_website",
            "Emit index_document { suffix = \"index.html\" } unless the prompt names another index document.",
            ["index_document.suffix"],
            "semantic_contract:s3:website_configuration",
        )

    if "aws_route53_record" in selected_set and ("aws_elb" in selected_set or "aws_lb" in selected_set):
        if has_any("alias", "load balancer", "elastic load balancer", "elb", "alb", "nlb"):
            add(
                "aws_route53_record",
                "route53_load_balancer_alias",
                "Use alias { name = load_balancer.dns_name, zone_id = load_balancer.zone_id, evaluate_target_health = true } and omit records and ttl.",
                ["alias.name", "alias.zone_id", "alias.evaluate_target_health"],
                "semantic_contract:route53:load_balancer_alias",
            )
    if has_any("weighted routing", "weighted record", "weighted routing policy"):
        add(
            "aws_route53_record",
            "route53_weighted_record",
            "Emit weighted_routing_policy { weight = 1 } and set_identifier; do not create a separate weighted-routing resource.",
            ["weighted_routing_policy.weight", "set_identifier"],
            "semantic_contract:route53:weighted_record",
        )
    if "aws_route53_query_log" in selected_set or has_any("query log", "query logging"):
        add(
            "aws_cloudwatch_log_resource_policy",
            "route53_query_log_policy",
            "policy_document must allow service principal route53.amazonaws.com to perform logs:CreateLogStream and logs:PutLogEvents.",
            ["policy_document", "policy_name"],
            "semantic_contract:route53:query_log_resource_policy",
        )
        add(
            "aws_route53_query_log",
            "route53_query_log_references",
            "Reference aws_route53_zone.zone_id and aws_cloudwatch_log_group.arn through zone_id and cloudwatch_log_group_arn.",
            ["zone_id", "cloudwatch_log_group_arn"],
            "semantic_contract:route53:query_log_references",
        )

    if "aws_iam_role" in selected_set and has_any("lambda", "lambda function", "lambda execution role"):
        add(
            "aws_iam_role",
            "lambda_assume_role_policy",
            "assume_role_policy must allow service principal lambda.amazonaws.com to assume the role.",
            ["assume_role_policy"],
            "semantic_contract:iam:lambda_assume_role",
        )
    if "iam_actions" in slots and ("aws_iam_policy" in selected_set or "aws_iam_role_policy" in selected_set):
        add(
            "aws_iam_policy" if "aws_iam_policy" in selected_set else "aws_iam_role_policy",
            "preserve_prompt_iam_actions",
            "Include the IAM actions explicitly visible in the prompt in the generated policy JSON.",
            ["policy"],
            "semantic_contract:iam:prompt_actions",
        )

    if "ports" in slots and ("aws_security_group" in selected_set or "aws_security_group_rule" in selected_set):
        target_type = "aws_security_group_rule" if "aws_security_group_rule" in selected_set else "aws_security_group"
        protocol = (slots.get("protocols") or ["tcp"])[0]
        add(
            target_type,
            "security_group_prompt_ports",
            f"For every visible prompt port, set from_port and to_port to that port and protocol to {protocol}.",
            ["from_port", "to_port", "protocol"],
            "semantic_contract:security_group:prompt_ports",
        )

    if has_any("public subnet", "internet gateway", "internet access", "public route"):
        add(
            "aws_subnet",
            "public_subnet_defaults",
            "For a public subnet, set map_public_ip_on_launch = true and use a valid subnet CIDR inside the generated VPC CIDR.",
            ["map_public_ip_on_launch", "cidr_block", "vpc_id"],
            "semantic_contract:vpc:public_subnet",
        )
        add(
            "aws_route",
            "public_default_route",
            "For public internet access, create a 0.0.0.0/0 route through the generated internet gateway.",
            ["destination_cidr_block", "gateway_id"],
            "semantic_contract:vpc:public_default_route",
        )

    if "aws_db_instance" in selected_set:
        if has_any("mysql", "postgres", "postgresql", "mariadb", "sqlserver", "oracle"):
            add(
                "aws_db_instance",
                "preserve_database_engine",
                "Use the database engine family visible in the prompt and provide a compatible offline-valid instance_class and allocated_storage.",
                ["engine", "instance_class", "allocated_storage"],
                "semantic_contract:rds:engine_family",
            )
        if "aws_db_subnet_group" in selected_set:
            add(
                "aws_db_instance",
                "rds_subnet_group_reference",
                "Use db_subnet_group_name = aws_db_subnet_group.<name>.name, not a top-level vpc_id.",
                ["db_subnet_group_name"],
                "semantic_contract:rds:db_subnet_group",
            )

    # Keep the contract compact and stable for small models.
    return obligations[:20]


def retrieve_public_provider_contract_evidence(
    prompt,
    include_semantic=None,
    use_auto_patterns=None,
    use_static_bundles=None,
    include_static_semantic=None,
):
    root = configured_root()
    index = _load_contract_index(str(root))
    versioning.assert_version_alignment(
        index.get("metadata", {}).get("provider_version", "")
    )
    if include_semantic is None:
        include_semantic = os.environ.get("IAC_CONTRACT_INCLUDE_SEMANTIC", "0").lower() in {
            "1",
            "true",
            "yes",
        }
    if use_auto_patterns is None:
        use_auto_patterns = os.environ.get("IAC_CONTRACT_USE_EXAMPLE_PATTERNS", "0").lower() in {
            "1",
            "true",
            "yes",
        }
    if use_static_bundles is None:
        use_static_bundles = os.environ.get("IAC_CONTRACT_USE_STATIC_BUNDLES", "1").lower() in {
            "1",
            "true",
            "yes",
        }
    if include_static_semantic is None:
        include_static_semantic = os.environ.get("IAC_CONTRACT_USE_STATIC_SEMANTIC", "1").lower() in {
            "1",
            "true",
            "yes",
        }
    selected, reasons, bundles, prompt_tokens, example_patterns, scores = _select_resources(
        prompt,
        index,
        use_static_bundles=use_static_bundles,
        use_example_patterns=use_auto_patterns,
    )
    deps = _selected_dependencies(index, selected, prompt_tokens)
    value_bindings, slots = _value_bindings(prompt, selected)

    candidate_resources = [
        _candidate_resource(
            resource_type,
            index["contracts"][resource_type],
            reasons,
            scores.get(resource_type, 0),
        )
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
        "contract_kind": (
            "public_provider_auto_semantic_contract_graph_v1"
            if include_semantic and use_auto_patterns and not include_static_semantic
            else "public_provider_semantic_contract_graph_v1"
            if include_semantic
            else "public_provider_contract_graph_v1"
        ),
        "source_policy": (
            "Built from the full public Terraform AWS provider schema and official provider docs/examples "
            "under the configured public KG root. Runtime retrieval uses only the visible prompt. "
            "No IaC-Eval Resource, Intent, Rego intent, Reference output, validation result, plan result, "
            "OPA result, generated HCL, or repair trace is used."
        ),
        "retrieval_method": {
            "retriever_version": versioning.RETRIEVER_VERSION,
            "mode": os.environ.get("IAC_RETRIEVAL_MODE", "hybrid_graph"),
            "entity_linking": "exact/alias + BM25 + optional dense semantic retrieval",
            "dependency_reranking": "provenance-aware public KG reference templates",
            "dependency_closure": "conditional expansion; REQUIRES_VALUE_OF_TYPE does not imply managed-resource creation",
            "retrieval_rules_sha256": retrieval_rules_sha256(),
            "retrieval_parameters": {
                "min_resources": MIN_RESOURCES,
                "max_resources": MAX_RESOURCES,
                "max_dependencies": MAX_DEPENDENCIES,
                "score_threshold": RESOURCE_SCORE_THRESHOLD,
                "score_gap": RESOURCE_SCORE_GAP,
            },
            "injection_granularity": {
                "ir": "resource candidates, dependency closure, required resource hints, prompt slots",
                "hcl": "argument contracts, nested block contracts, reference templates, usage constraints, value bindings",
            },
            "package_root": index["root"],
            "provider_version": index.get("metadata", {}).get("provider_version", ""),
            "example_pattern_graph": "official provider examples parsed into resource bundles, reference templates, nested blocks, and literal hints"
            if use_auto_patterns
            else "",
        },
        "candidate_resources": candidate_resources,
        "candidate_scores": [
            {
                "type": item["type"],
                "score": item["score"],
                "matched_by": item["matched_by"],
            }
            for item in candidate_resources
        ],
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
        "matched_example_patterns": [
            {
                "id": pattern.get("id", ""),
                "name": pattern.get("name", ""),
                "source_resource_type": pattern.get("source_resource_type", ""),
                "resource_types": [r for r in pattern.get("resource_types", []) if r in selected],
                "source_doc": pattern.get("source_doc", ""),
            }
            for pattern in example_patterns
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
            "semantic_obligations": _semantic_obligations(
                prompt,
                selected,
                deps,
                slots,
                example_patterns=example_patterns,
                include_static=include_static_semantic,
            )
            if include_semantic
            else [],
        },
    }
    return json.dumps(evidence, indent=2, sort_keys=True)


retrieve_provider_contract_evidence = retrieve_public_provider_contract_evidence


def retrieve_public_provider_semantic_contract_evidence(prompt):
    return retrieve_public_provider_contract_evidence(prompt, include_semantic=True)


def retrieve_public_provider_auto_semantic_contract_evidence(prompt):
    return retrieve_public_provider_contract_evidence(
        prompt,
        include_semantic=True,
        use_auto_patterns=True,
        use_static_bundles=False,
        include_static_semantic=False,
    )
