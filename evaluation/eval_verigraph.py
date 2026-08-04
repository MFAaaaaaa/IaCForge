"""Paper-facing IaCForge evaluation pipeline.

Generation is restricted to the visible Prompt plus public provider
schema/KG evidence. Hidden IaC-Eval columns are accessed only after generation
for evaluation.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd

import graph_ir
import evidence_projection
import hcl_metrics
import ir_schema_checker
import local_repair
import models
import opa_evaluator
import prompt_templates_verigraph as prompt_templates
import result_metrics
import schema_rag
import provider_contract as provider_contract_builder
import versioning


DEFAULT_TERRAFORM_VERSION_CONSTRAINT = "~> 1.9.8"
DEFAULT_AWS_PROVIDER_VERSION_CONSTRAINT = f"= {versioning.AWS_PROVIDER_VERSION}"
DEFAULT_TERRAFORM_PLAN_TIMEOUT_SECONDS = 100
RESULT_COLUMN_BASES = [
    "LLM Output #",
    "LLM Compilable? #",
    "LLM Plannable? #",
    "LLM Correct? #",
    "LLM Compile Phase Error #",
    "LLM Plan Phase Error #",
    "LLM OPA match phase Error #",
    "LLM Notes #",
]

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT_DIR / "data" / "complete" / "data.csv"
PLANNER_SYSTEM_PROMPT = (
    "You are a deterministic Infrastructure-as-Code task planner. Return only "
    "the requested typed JSON task graph. Never use hidden benchmark labels, "
    "reference outputs, evaluator policies, or feedback traces."
)
COMPILER_SYSTEM_PROMPT = (
    "You are a deterministic Infrastructure-as-Code compiler. Emit concise, "
    "complete, offline-valid Terraform HCL that exactly implements the supplied "
    "typed contract. Never use hidden benchmark labels, reference outputs, "
    "evaluator policies, or feedback traces."
)
SYSTEM_PROMPT = COMPILER_SYSTEM_PROMPT

logger = logging.getLogger("iacforge")


def setup_logging(log_file):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )


def load_config(path):
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    return int(config.get("samples", 1)), list(config.get("models", []))


def _read_row_ids(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        values = json.loads(text)
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    return [int(value) for value in values]


def selected_rows(max_rows, row_ids_file=None):
    df = pd.read_csv(DATA_FILE)
    df.insert(0, "Evaluation Row ID", list(range(len(df))))
    if row_ids_file:
        row_ids = _read_row_ids(row_ids_file)
        missing = sorted(set(row_ids) - set(df["Evaluation Row ID"]))
        if missing:
            raise ValueError(f"Row IDs are outside the dataset: {missing[:20]}")
        order = {value: index for index, value in enumerate(row_ids)}
        df = df[df["Evaluation Row ID"].isin(row_ids)].copy()
        df["_selection_order"] = df["Evaluation Row ID"].map(order)
        df = df.sort_values("_selection_order").drop(columns=["_selection_order"])
    if max_rows is not None:
        df = df.head(max_rows).copy()
    return df.reset_index(drop=True)


def terraform_constraints_block():
    terraform_version = os.environ.get(
        "TERRAFORM_VERSION_CONSTRAINT", DEFAULT_TERRAFORM_VERSION_CONSTRAINT
    )
    aws_provider_version = os.environ.get(
        "AWS_PROVIDER_VERSION_CONSTRAINT", DEFAULT_AWS_PROVIDER_VERSION_CONSTRAINT
    )
    return f'''terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "{aws_provider_version}"
    }}
  }}

  required_version = "{terraform_version}"
}}
'''


def aws_provider_block():
    return '''provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}
'''


def add_static_provider_settings(match):
    block = match.group(0)
    insertions = []
    for key in (
        "skip_credentials_validation",
        "skip_requesting_account_id",
        "skip_metadata_api_check",
        "skip_region_validation",
    ):
        if not re.search(rf"^\s*{key}\s*=", block, flags=re.MULTILINE):
            insertions.append(f"  {key:<27} = true")
    if not insertions:
        return block
    return block[:-1].rstrip() + "\n" + "\n".join(insertions) + "\n}"


def normalize_terraform_config(result, static_plan):
    config = str(result or "").strip()
    if "required_providers" not in config or "hashicorp/aws" not in config:
        config = terraform_constraints_block() + "\n" + config
    if not re.search(r'provider\s+"aws"\s*{', config):
        config = config + "\n\n" + aws_provider_block()
    elif static_plan:
        config = re.sub(
            r'provider\s+"aws"\s*{[^{}]*}',
            add_static_provider_settings,
            config,
            flags=re.DOTALL,
        )
    return config + "\n"


def normalize_terraform_config_with_diff(result, static_plan):
    raw = str(result or "").strip()
    normalized = normalize_terraform_config(raw, static_plan)
    diff = "\n".join(
        difflib.unified_diff(
            raw.splitlines(),
            normalized.splitlines(),
            fromfile="raw_llm_hcl",
            tofile="normalized_hcl",
            lineterm="",
        )
    )
    return normalized, diff


def extract_hcl(text):
    text = str(text or "")
    for marker in ("hcl", "terraform", "HCL", ""):
        pattern = rf"```{marker}\s*(.*?)\s*```" if marker else r"```\s*(.*?)\s*```"
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
    return text.strip()


def command_available(name):
    return shutil.which(name) is not None


def configured_provider_mirror():
    value = os.environ.get("IAC_PROVIDER_MIRROR", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    bundled = ROOT_DIR / "data" / "provider_mirror"
    return bundled.resolve() if bundled.exists() else None


def terraform_cli_config_for_mirror(mirror):
    mirror = Path(mirror).resolve()
    digest = hashlib.sha256(str(mirror).encode("utf-8")).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"iacforge-terraformrc-{digest}"
    content = f'''provider_installation {{
  filesystem_mirror {{
    path    = "{mirror.as_posix()}"
    include = ["registry.terraform.io/hashicorp/aws"]
  }}
}}
'''
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def command_env(static_plan):
    env = os.environ.copy()
    if static_plan:
        env.update(
            {
                "AWS_ACCESS_KEY_ID": "offline",
                "AWS_SECRET_ACCESS_KEY": "offline",
                "AWS_SESSION_TOKEN": "offline",
                "AWS_DEFAULT_REGION": "us-east-1",
                "AWS_REGION": "us-east-1",
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_SDK_LOAD_CONFIG": "0",
                "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
                "AWS_CONFIG_FILE": os.devnull,
            }
        )
        env.pop("AWS_PROFILE", None)
        env.pop("AWS_DEFAULT_PROFILE", None)

    explicit_config = os.environ.get("IAC_TERRAFORM_CLI_CONFIG_FILE", "").strip()
    if explicit_config:
        config_path = Path(explicit_config).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"IAC_TERRAFORM_CLI_CONFIG_FILE does not exist: {config_path}")
        env["TF_CLI_CONFIG_FILE"] = str(config_path)
    else:
        mirror = configured_provider_mirror()
        if mirror:
            if not mirror.exists():
                raise FileNotFoundError(f"IAC_PROVIDER_MIRROR does not exist: {mirror}")
            env["TF_CLI_CONFIG_FILE"] = str(terraform_cli_config_for_mirror(mirror))
    return env


def run_cmd(args, cwd, timeout, static_plan=True):
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=command_env(static_plan),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or f"Timed out after {timeout}s"
    return completed.returncode, completed.stdout, completed.stderr


def terraform_validate(workdir, static_plan):
    if not command_available("terraform"):
        return False, "terraform executable not found"
    timeout = int(
        os.environ.get("TERRAFORM_PLAN_TIMEOUT_SECONDS", DEFAULT_TERRAFORM_PLAN_TIMEOUT_SECONDS)
    )
    init_code, init_out, init_err = run_cmd(
        ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
        workdir,
        timeout,
        static_plan,
    )
    if init_code != 0:
        return False, (init_out + "\n" + init_err).strip()
    code, out, err = run_cmd(
        ["terraform", "validate", "-no-color"], workdir, timeout, static_plan
    )
    return code == 0, (out + "\n" + err).strip()


def terraform_plan_json(workdir, static_plan):
    if not command_available("terraform"):
        return False, None, "terraform executable not found"
    timeout = int(
        os.environ.get("TERRAFORM_PLAN_TIMEOUT_SECONDS", DEFAULT_TERRAFORM_PLAN_TIMEOUT_SECONDS)
    )
    plan_path = Path(workdir) / "tfplan"
    command = ["terraform", "plan"]
    if static_plan:
        command.append("-refresh=false")
    command.extend(
        ["-input=false", "-lock=false", "-no-color", "-out", str(plan_path)]
    )
    code, out, err = run_cmd(command, workdir, timeout, static_plan)
    if code != 0:
        return False, None, (out + "\n" + err).strip()
    code, out, err = run_cmd(
        ["terraform", "show", "-json", str(plan_path)], workdir, timeout, static_plan
    )
    if code != 0:
        return False, None, (out + "\n" + err).strip()
    plan_json = Path(workdir) / "plan.json"
    plan_json.write_text(out, encoding="utf-8")
    return True, plan_json, ""


def mode_from_strategy(strategy):
    explicit = os.environ.get("IACFORGE_MODE", "").strip()
    if explicit:
        return explicit
    if strategy == "FullKGVeriGraph":
        return "full"
    if strategy == "VeriGraph" and os.environ.get(
        "VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING", "0"
    ).lower() in {"0", "false", "no"}:
        return "ir_only"
    if strategy == "VeriGraph":
        return "ir_schema"
    return "baseline"


def result_category(mode):
    categories = {
        "baseline": "baseline",
        "ir_only": "ir_only_ablation",
        "ir_schema": "ir_schema_grounding",
        "planner_kg": "planner_kg_ablation",
        "compiler_kg": "compiler_kg_ablation",
        "full": "full_ir_schema_grounding_kg",
        "full_strict": "full_strict",
        "full_repair1": "full_repair1",
    }
    return categories[mode]


def _graph_details(raw_graph_ir):
    validation = graph_ir.safe_parse_graph_ir(raw_graph_ir)
    return validation, graph_ir.provenance("", validation)


def _generation_note(output):
    return {
        "model": output.model,
        "stage": output.stage,
        "input_tokens": output.input_tokens,
        "output_tokens": output.output_tokens,
        "latency_ms": output.latency_ms,
        "request_parameters": output.request_parameters,
    }


def _version_manifest():
    kg_root_value = (
        os.environ.get("IAC_PROVIDER_CONTRACT_ROOT", "").strip()
        or os.environ.get("IAC_KG_REPLICATION_ROOT", "").strip()
    )
    kg_root = Path(kg_root_value) if kg_root_value else None
    return versioning.build_version_manifest(
        schema_rag.provider_schema.SCHEMA_FILE,
        kg_root if kg_root and kg_root.exists() else None,
    )


@lru_cache(maxsize=2)
def _load_legacy_evidence_index(path_text):
    index = {}
    with Path(path_text).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            key = row.get("prompt_sha256") or graph_ir.prompt_sha256(
                row.get("prompt", "")
            )
            index.setdefault(key, []).append(row)
    return index


def _kg_evidence_for_prompt(question_prompt):
    profile = os.environ.get("IAC_KG_PROFILE", "clean_multigranular").strip().lower()
    if profile == "paper":
        allowed = os.environ.get(
            "IAC_ALLOW_BENCHMARK_SCOPED_PAPER_KG", "0"
        ).lower() in {"1", "true", "yes"}
        if not allowed:
            raise ValueError(
                "The paper KG may be benchmark-scoped. Set "
                "IAC_ALLOW_BENCHMARK_SCOPED_PAPER_KG=1 only for the explicitly "
                "labelled paper-reference experiments."
            )
        paper_root = ROOT_DIR / "data" / "paper_kg" / "source"
        paper_chroma = ROOT_DIR / "data" / "paper_kg" / "chroma"
        os.environ.setdefault("IAC_KG_PAPER_REPLICATION_ROOT", str(paper_root))
        os.environ.setdefault("IAC_KG_PAPER_CHROMA_DIR", str(paper_chroma))
        from iac_kg.paper_replication_json_retriever import (
            retrieve_paper_replication_json_evidence,
        )

        parsed = json.loads(retrieve_paper_replication_json_evidence(question_prompt))
        return parsed, {
            "source": "paper_replication_json",
            "profile": profile,
            "prompt_sha256": graph_ir.prompt_sha256(question_prompt),
            "evidence_sha256": versioning.canonical_sha256(parsed),
            "leakage_class": "paper_reference_potentially_benchmark_scoped",
        }

    if profile in {"hybrid", "hybrid_cached_evidence_rebuilt_kg"}:
        evidence_path = Path(
            os.environ.get(
                "IAC_HYBRID_EVIDENCE_FILE",
                ROOT_DIR
                / "data"
                / "hybrid_paper_fullkg"
                / "evidence_v1"
                / "publickg_full458.jsonl",
            )
        )
        prompt_hash = graph_ir.prompt_sha256(question_prompt)
        evidence = None
        for row in _load_legacy_evidence_index(str(evidence_path.resolve())).get(
            prompt_hash, []
        ):
            if row.get("prompt") not in {None, question_prompt}:
                continue
            evidence = row.get("evidence")
            break
        if evidence is None:
            raise KeyError(
                f"Hybrid evidence-v1 has no entry for prompt_sha256={prompt_hash}"
            )
        parsed = json.loads(evidence) if isinstance(evidence, str) else evidence
        return parsed, {
            "source": "first_build_offline_evidence",
            "profile": "hybrid_cached_evidence_rebuilt_kg",
            "prompt_sha256": prompt_hash,
            "evidence_sha256": versioning.canonical_sha256(parsed),
            "evidence_build": "v1_first_build",
            "kg_build": "v2_rebuilt_after_original_loss",
            "version_alignment": "best_effort_content_may_differ_slightly",
            "leakage_class": "historical_semi_clean_reference",
        }

    if profile != "clean_multigranular":
        raise ValueError(f"Unknown IAC_KG_PROFILE: {profile}")

    from iac_kg.offline_provider_contract_cache import (
        configured_cache_path,
        get_offline_provider_contract_entry,
    )

    entry = get_offline_provider_contract_entry(question_prompt)
    source = "offline_provider_contract_cache"
    if entry is None:
        allow_online = os.environ.get("IAC_ALLOW_ONLINE_KG_RETRIEVAL", "0").lower() in {
            "1",
            "true",
            "yes",
        }
        if not allow_online:
            raise FileNotFoundError(
                "Offline KG evidence missing for prompt_sha256="
                f"{graph_ir.prompt_sha256(question_prompt)} in {configured_cache_path()}. "
                "Rebuild the cache with the current retriever/version manifest."
            )
        from iac_kg.provider_contract_retriever import (
            retrieve_public_provider_contract_evidence,
        )

        parsed = json.loads(
            retrieve_public_provider_contract_evidence(question_prompt)
        )
        entry = {
            "prompt_sha256": graph_ir.prompt_sha256(question_prompt),
            "retriever_version": versioning.RETRIEVER_VERSION,
            "provider_version": versioning.AWS_PROVIDER_VERSION,
            "evidence": parsed,
            "evidence_sha256": versioning.canonical_sha256(parsed),
            "retrieval_parameters": parsed.get("retrieval_method", {}).get(
                "retrieval_parameters", {}
            ),
            "candidate_scores": parsed.get("candidate_scores", []),
        }
        source = "online_provider_contract_retriever"
    return entry["evidence"], {
        "source": source,
        "profile": profile,
        "prompt_sha256": entry.get("prompt_sha256", ""),
        "retriever_version": entry.get("retriever_version", ""),
        "provider_version": entry.get("provider_version", ""),
        "kg_sha256": entry.get("kg_sha256", ""),
        "schema_sha256": entry.get("schema_sha256", ""),
        "evidence_sha256": entry.get("evidence_sha256")
        or versioning.canonical_sha256(entry["evidence"]),
        "retrieval_parameters": entry.get("retrieval_parameters", {}),
        "candidate_scores": entry.get("candidate_scores", []),
    }


def build_generation_prompt(mode, question_prompt):
    if mode == "baseline":
        notes = {
            "mode": "baseline",
            "prompt_sha256": graph_ir.prompt_sha256(question_prompt),
            "generation_inputs": ["Prompt"],
            "version_manifest": _version_manifest(),
        }
        return prompt_templates.baseline_generation_prompt(question_prompt), notes

    kg_modes = {"planner_kg", "compiler_kg", "full", "full_strict", "full_repair1"}
    if mode == "planner_kg":
        injection_stage = "ir"
    elif mode == "compiler_kg":
        injection_stage = "hcl"
    else:
        injection_stage = os.environ.get(
            "IAC_KG_INJECTION_STAGE",
            os.environ.get("VERIGRAPH_KG_INJECTION_STAGE", "both"),
        ).strip().lower()
    if injection_stage not in {"ir", "hcl", "both"}:
        raise ValueError(f"Unknown KG injection stage: {injection_stage}")
    planner_kg_enabled = mode in kg_modes and injection_stage in {"ir", "both"}
    compiler_kg_enabled = mode in kg_modes and injection_stage in {"hcl", "both"}
    kg_evidence = {}
    kg_note = None
    planner_evidence = {}
    retrieval_ms = 0
    if mode in kg_modes:
        started = time.perf_counter()
        kg_evidence, kg_note = _kg_evidence_for_prompt(question_prompt)
        retrieval_ms = round((time.perf_counter() - started) * 1000)
    if planner_kg_enabled:
        planner_evidence = evidence_projection.project_planner_evidence(kg_evidence)
        graph_prompt = prompt_templates.full_kg_resource_graph_ir_prompt_only_prompt(
            question_prompt,
            evidence_projection.render_planner_evidence(planner_evidence),
        )
    else:
        graph_prompt = prompt_templates.resource_graph_ir_prompt_only_prompt(question_prompt)

    ir_output = models.generate_with_metadata(
        PLANNER_SYSTEM_PROMPT,
        graph_prompt,
        stage="ir",
        guided_json=True,
    )
    raw_graph_ir = ir_output.text
    validation, graph_note = _graph_details(raw_graph_ir)
    graph_note["prompt_sha256"] = graph_ir.prompt_sha256(question_prompt)
    normalized_graph = validation.graph
    graph_note["planner_evidence_sha256"] = (
        versioning.canonical_sha256(planner_evidence) if planner_evidence else ""
    )

    if mode == "ir_only":
        prompt = prompt_templates.resource_graph_ir_generation_prompt(
            question_prompt, graph_ir.render_graph_ir(normalized_graph)
        )
        return prompt, {
            "mode": mode,
            "generation_inputs": ["Prompt", "Graph IR"],
            "graph_ir": graph_note,
            "generation_cost": {
                "retrieval_ms": retrieval_ms,
                "ir": _generation_note(ir_output),
            },
            "_normalized_ir": normalized_graph,
            "_provider_contract": {},
            "_schema_context": "",
        }

    schema_check = ir_schema_checker.check_graph_ir(normalized_graph)
    normalized_graph = schema_check.graph
    graph_note["schema_consistency"] = schema_check.as_dict()
    schema = schema_rag.retrieve_schema_for_graph(normalized_graph, question_prompt)
    if compiler_kg_enabled:
        provider_contract = provider_contract_builder.build_provider_contract(
            question_prompt,
            normalized_graph,
            schema.projection,
            kg_evidence,
        )
        contract_validation = provider_contract_builder.validate_provider_contract(
            provider_contract
        )
        use_skeleton = os.environ.get("IAC_USE_HCL_SKELETON", "1").lower() in {
            "1",
            "true",
            "yes",
        }
        skeleton = (
            provider_contract_builder.build_hcl_skeleton(provider_contract)
            if use_skeleton
            else ""
        )
        prompt = prompt_templates.resource_graph_ir_schema_contract_generation_prompt(
            question_prompt,
            graph_ir.render_graph_ir(normalized_graph),
            schema.context,
            provider_contract_builder.render_provider_contract(provider_contract),
            skeleton,
        )
        inputs = [
            "Prompt",
            "normalized typed Graph IR",
            "IR-guided exact provider schema",
            "task-specific canonical Provider Contract",
        ]
    else:
        provider_contract = {}
        contract_validation = {"valid": True, "violations": []}
        prompt = prompt_templates.resource_graph_ir_schema_generation_prompt(
            question_prompt,
            graph_ir.render_graph_ir(normalized_graph),
            schema.context,
        )
        inputs = ["Prompt", "normalized typed Graph IR", "IR-guided exact provider schema"]
    return prompt, {
        "mode": mode,
        "version_manifest": _version_manifest(),
        "generation_inputs": inputs,
        "graph_ir": graph_note,
        "schema_rag": schema.as_dict(),
        "kg": kg_note,
        "retrieval": {
            "kg_profile": os.environ.get("IAC_KG_PROFILE", "clean_multigranular"),
            "injection_stage": injection_stage,
            "candidate_types": [
                item.get("type", "")
                for item in planner_evidence.get("candidate_resources", [])
            ],
            "scores": [
                item.get("score", 0)
                for item in planner_evidence.get("candidate_resources", [])
            ],
            "matched_rules": [
                item.get("matched_by", [])
                for item in planner_evidence.get("candidate_resources", [])
            ],
            "planner_evidence_sha256": (
                versioning.canonical_sha256(planner_evidence)
                if planner_evidence
                else ""
            ),
        },
        "compiler_contract": {
            "contract_sha256": provider_contract.get("contract_sha256", ""),
            "resource_instances": sorted(
                provider_contract.get("instance_contracts", {})
            ),
            "bindings": provider_contract.get("bindings", []),
            "validation": contract_validation,
        },
        "generation_cost": {
            "retrieval_ms": retrieval_ms,
            "ir": _generation_note(ir_output),
        },
        "_normalized_ir": normalized_graph,
        "_provider_contract": provider_contract,
        "_schema_context": schema.context,
    }


def evaluate_one(row, mode, static_plan):
    result = {base + "0": "" for base in RESULT_COLUMN_BASES}
    # Leakage boundary: generation sees only Prompt and public evidence.
    question_prompt = row.get("Prompt", "")
    try:
        generation_prompt, notes = build_generation_prompt(mode, question_prompt)
        normalized_ir = notes.pop("_normalized_ir", graph_ir.empty_graph_ir())
        compiler_contract = notes.pop("_provider_contract", {})
        schema_context = notes.pop("_schema_context", "")
        resource_count = len(
            compiler_contract.get("instance_contracts", {})
            or normalized_ir.get("resources", [])
        )
        hcl_max_tokens = min(4096, max(1536, 1024 + 384 * resource_count))
        hcl_output = models.generate_with_metadata(
            COMPILER_SYSTEM_PROMPT,
            generation_prompt,
            stage="hcl",
            max_tokens=hcl_max_tokens,
        )
        raw_llm_hcl = extract_hcl(hcl_output.text)
        hcl, normalization_diff = normalize_terraform_config_with_diff(
            raw_llm_hcl, static_plan
        )
        notes.setdefault("generation_cost", {})["hcl"] = _generation_note(hcl_output)
        notes["hcl_generation"] = {
            "raw_llm_hcl": raw_llm_hcl,
            "normalized_hcl": hcl,
            "normalization_diff": normalization_diff,
            "mechanism_metrics": hcl_metrics.analyze_hcl(
                hcl, normalized_ir, compiler_contract
            ),
        }
        result["LLM Output #0"] = hcl
        result["LLM Notes #0"] = json.dumps(notes, ensure_ascii=True, sort_keys=True)
    except Exception as exc:
        result["LLM Compile Phase Error #0"] = f"generation failed: {exc}"
        return result

    with tempfile.TemporaryDirectory(prefix="iacforge-") as tmp:
        workdir = Path(tmp)
        (workdir / "main.tf").write_text(hcl, encoding="utf-8")
        compilable, compile_error = terraform_validate(workdir, static_plan)
        initial_validate = bool(compilable)
        initial_plan = False
        repair_used = False

        def repair_once(diagnostic):
            nonlocal hcl, raw_llm_hcl, normalization_diff, repair_used
            repair_prompt = local_repair.build_prompt(
                question_prompt,
                graph_ir.render_graph_ir(normalized_ir),
                schema_context,
                hcl,
                diagnostic,
            )
            output = models.generate_with_metadata(
                COMPILER_SYSTEM_PROMPT,
                repair_prompt,
                stage="repair",
                max_tokens=hcl_max_tokens,
            )
            raw_llm_hcl = extract_hcl(output.text)
            hcl, normalization_diff = normalize_terraform_config_with_diff(
                raw_llm_hcl, static_plan
            )
            (workdir / "main.tf").write_text(hcl, encoding="utf-8")
            repair_used = True
            notes["repair"] = local_repair.policy_manifest()
            notes["repair"]["calls"] = 1
            notes["repair"]["generation"] = _generation_note(output)
            notes["hcl_generation"]["repaired_raw_llm_hcl"] = raw_llm_hcl
            notes["hcl_generation"]["repaired_normalized_hcl"] = hcl
            notes["hcl_generation"]["repair_normalization_diff"] = normalization_diff

        result["LLM Compilable? #0"] = bool(compilable)
        result["LLM Compile Phase Error #0"] = "" if compilable else compile_error
        if not compilable:
            notes["repair_outcome"] = {
                "initial_validate": initial_validate,
                "final_validate": bool(compilable),
                "repair_used": repair_used,
            }
            result["LLM Output #0"] = hcl
            result["LLM Notes #0"] = json.dumps(
                notes, ensure_ascii=True, sort_keys=True
            )
            return result

        plannable, plan_json, plan_error = terraform_plan_json(workdir, static_plan)
        initial_plan = bool(plannable)
        if not plannable and mode == "full_repair1":
            repair_once(plan_error)
            compilable, compile_error = terraform_validate(workdir, static_plan)
            result["LLM Compilable? #0"] = bool(compilable)
            result["LLM Compile Phase Error #0"] = (
                "" if compilable else compile_error
            )
            if compilable:
                plannable, plan_json, plan_error = terraform_plan_json(
                    workdir, static_plan
                )
            else:
                plannable, plan_json, plan_error = False, {}, compile_error
        result["LLM Plannable? #0"] = bool(plannable)
        result["LLM Plan Phase Error #0"] = "" if plannable else plan_error
        notes["repair_outcome"] = {
            "initial_validate": initial_validate,
            "initial_plan": initial_plan,
            "final_validate": bool(compilable),
            "final_plan": bool(plannable),
            "repair_used": repair_used,
        }
        notes["hcl_generation"]["mechanism_metrics"] = hcl_metrics.analyze_hcl(
            hcl, normalized_ir, compiler_contract
        )
        result["LLM Output #0"] = hcl
        result["LLM Notes #0"] = json.dumps(
            notes, ensure_ascii=True, sort_keys=True
        )
        if not plannable:
            return result

        # Hidden evaluator policy is accessed only after HCL generation and plan.
        correct, opa_error = opa_evaluator.opa_evaluate(
            plan_json,
            row.get("Rego intent", ""),
            lambda args, cwd, timeout: run_cmd(args, cwd, timeout, static_plan),
        )
        result["LLM Correct? #0"] = bool(correct)
        result["LLM OPA match phase Error #0"] = "" if correct else opa_error
    return result


def output_paths(model_name, mode, suffix):
    file_name = f"evaluation-dataset-for-data{suffix}.csv"
    tmp_path = Path("tmp") / model_name / "complete" / file_name
    results_root = Path(os.environ.get("EVAL_RESULTS_ROOT", ROOT_DIR / "results"))
    result_path = results_root / result_category(mode) / model_name / file_name
    return tmp_path, result_path


def atomic_write_csv(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temporary, index=False)
    os.replace(temporary, path)


def restore_checkpoint(df, candidates):
    checkpoint = next((Path(path) for path in candidates if Path(path).exists()), None)
    if not checkpoint:
        return df, None
    previous = pd.read_csv(checkpoint)
    if "Evaluation Row ID" not in previous.columns:
        previous.insert(0, "Evaluation Row ID", list(range(len(previous))))
    previous = previous.set_index("Evaluation Row ID")
    restored = df.set_index("Evaluation Row ID")
    for column in [base + "0" for base in RESULT_COLUMN_BASES]:
        if column in previous.columns:
            restored[column] = previous[column].reindex(restored.index)
    return restored.reset_index(), checkpoint


def row_completed(row):
    return bool(
        str(row.get("LLM Output #0", "") or "").strip()
        or str(row.get("LLM Compile Phase Error #0", "") or "").strip()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--enhance-strat", default="")
    parser.add_argument("--static-plan", action="store_true")
    parser.add_argument("--max-rows", type=int, default=458)
    parser.add_argument("--row-ids-file")
    parser.add_argument("--log-file", default="logs/eval.log")
    parser.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--checkpoint-every", type=int, default=1)
    args = parser.parse_args()

    setup_logging(args.log_file)
    samples, config_models = load_config(args.config)
    if samples != 1:
        raise ValueError("Paper-facing package supports samples=1 only.")
    if len(config_models) != 1:
        raise ValueError("Model config must contain exactly one model name.")
    if args.checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be at least 1.")

    mode = mode_from_strategy(args.enhance_strat)
    suffix = os.environ.get("EVAL_OUTPUT_SUFFIX", f"-{mode}-full{args.max_rows}")
    output_model_name = os.environ.get("EVAL_MODEL_DIR", config_models[0])
    tmp_path, result_path = output_paths(output_model_name, mode, suffix)

    df = selected_rows(args.max_rows, args.row_ids_file)
    for base in RESULT_COLUMN_BASES:
        df[base + "0"] = ""
    if args.resume:
        df, checkpoint = restore_checkpoint(df, [tmp_path, result_path])
        if checkpoint:
            logger.info("Resuming from %s", checkpoint)

    logger.info(
        "Running model=%s mode=%s rows=%s provider=%s",
        config_models[0],
        mode,
        len(df),
        os.environ.get(
            "AWS_PROVIDER_VERSION_CONSTRAINT",
            DEFAULT_AWS_PROVIDER_VERSION_CONSTRAINT,
        ),
    )
    dirty = 0
    for index, row in df.iterrows():
        if args.resume and row_completed(row):
            logger.info("Skipping completed row_id=%s", row["Evaluation Row ID"])
            continue
        logger.info("Evaluating row_id=%s", row["Evaluation Row ID"])
        row_result = evaluate_one(row, mode, args.static_plan)
        for key, value in row_result.items():
            df.at[index, key] = value
        dirty += 1
        if dirty % args.checkpoint_every == 0:
            atomic_write_csv(df, tmp_path)
            atomic_write_csv(df, result_path)

    atomic_write_csv(df, tmp_path)
    atomic_write_csv(df, result_path)
    summary = result_metrics.summarize_rows(df.to_dict(orient="records"))
    logger.info("Wrote %s", tmp_path)
    logger.info("Wrote %s", result_path)
    logger.info("Summary %s", json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
