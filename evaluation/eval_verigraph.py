"""IaCForge evaluation pipeline.

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
from pathlib import Path

import pandas as pd

import graph_ir
import local_repair
import models
import opa_evaluator
import prompt_templates_verigraph as prompt_templates
import result_metrics
import schema_rag
import provider_contract as provider_contract_builder


DEFAULT_TERRAFORM_VERSION_CONSTRAINT = "~> 1.9.8"
DEFAULT_AWS_PROVIDER_VERSION_CONSTRAINT = "= 5.90.0"
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
    "You are an Infrastructure-as-Code generator. Return concise, complete, "
    "offline-valid Terraform HCL for AWS. Do not use hidden benchmark labels, "
    "reference outputs, evaluator policies, or feedback traces."
)
COMPILER_SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT
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


def experiment_mode(kg_kind, repair):
    return f"{kg_kind}_kg" + ("_repair" if repair else "")


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


def _canonical_sha256(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _kg_evidence_for_prompt(question_prompt, kg_kind):
    if kg_kind == "paper":
        paper_root = ROOT_DIR / "data" / "paper_kg" / "source"
        paper_chroma = ROOT_DIR / "data" / "paper_kg" / "chroma"
        os.environ.setdefault("IAC_KG_PAPER_REPLICATION_ROOT", str(paper_root))
        os.environ.setdefault("IAC_KG_PAPER_CHROMA_DIR", str(paper_chroma))
        from iac_kg.paper_replication_json_retriever import (
            retrieve_paper_replication_json_evidence,
        )

        parsed = json.loads(retrieve_paper_replication_json_evidence(question_prompt))
        return parsed, {
            "source": "paper_kg",
            "kg": "paper",
            "prompt_sha256": graph_ir.prompt_sha256(question_prompt),
            "evidence_sha256": _canonical_sha256(parsed),
            "scope_notice": "paper KG contains benchmark-scoped relation edges and is reported separately from Full KG",
        }

    if kg_kind != "full":
        raise ValueError(f"Unknown KG: {kg_kind}")

    full_kg_root = ROOT_DIR / "data" / "full_kg" / "provider_kg"
    os.environ.setdefault("IAC_PROVIDER_CONTRACT_ROOT", str(full_kg_root))
    os.environ.setdefault("IAC_KG_REPLICATION_ROOT", str(full_kg_root))
    from iac_kg.provider_contract_retriever import (
        retrieve_public_provider_contract_evidence,
    )

    parsed = json.loads(retrieve_public_provider_contract_evidence(question_prompt))
    return parsed, {
        "source": "full_provider_kg",
        "kg": "full",
        "prompt_sha256": graph_ir.prompt_sha256(question_prompt),
        "evidence_sha256": _canonical_sha256(parsed),
    }


def build_generation_prompt(mode, question_prompt):
    if mode not in {"full_kg", "full_kg_repair", "paper_kg", "paper_kg_repair"}:
        raise ValueError(f"Unknown experiment mode: {mode}")
    kg_kind = "paper" if mode.startswith("paper") else "full"
    started = time.perf_counter()
    kg_evidence, kg_note = _kg_evidence_for_prompt(question_prompt, kg_kind)
    retrieval_ms = round((time.perf_counter() - started) * 1000)
    graph_prompt = prompt_templates.resource_graph_ir_kg_prompt(
        question_prompt,
        json.dumps(kg_evidence, ensure_ascii=False, indent=2, sort_keys=True),
    )

    ir_output = models.generate_with_metadata(
        PLANNER_SYSTEM_PROMPT,
        graph_prompt,
        stage="ir",
    )
    raw_graph_ir = ir_output.text
    validation, graph_note = _graph_details(raw_graph_ir)
    graph_note["prompt_sha256"] = graph_ir.prompt_sha256(question_prompt)
    normalized_graph = validation.graph

    schema = schema_rag.retrieve_schema_for_graph(normalized_graph, question_prompt)
    if kg_kind == "full":
        provider_contract = provider_contract_builder.build_provider_contract(
            question_prompt,
            normalized_graph,
            schema.projection,
            kg_evidence,
        )
        prompt = prompt_templates.resource_graph_ir_schema_contract_generation_prompt(
            question_prompt,
            raw_graph_ir,
            schema.context,
            provider_contract_builder.render_provider_contract(provider_contract),
        )
        inputs = [
            "Prompt",
            "Planner-generated Graph IR text",
            "IR-guided exact provider schema",
            "task-specific canonical Provider Contract",
        ]
        hcl_kg_input = "typed_provider_contract"
    else:
        provider_contract = {}
        prompt = prompt_templates.resource_graph_ir_schema_kg_generation_prompt(
            question_prompt,
            raw_graph_ir,
            schema.context,
            json.dumps(kg_evidence, ensure_ascii=False, indent=2, sort_keys=True),
        )
        inputs = [
            "Prompt",
            "Planner-generated Graph IR text",
            "IR-guided exact provider schema",
            "raw prompt-retrieved KG evidence",
        ]
        hcl_kg_input = "raw_kg_evidence"
    return prompt, {
        "mode": mode,
        "generation_inputs": inputs,
        "graph_ir": graph_note,
        "schema_rag": schema.as_dict(),
        "kg": kg_note,
        "retrieval": {
            "kg": kg_kind,
            "stages": ["ir", "hcl"],
            "candidate_types": [
                item.get("type", "")
                for item in kg_evidence.get("candidate_resources", [])
            ],
            "scores": [
                item.get("score", 0)
                for item in kg_evidence.get("candidate_resources", [])
            ],
            "matched_rules": [
                item.get("matched_by", item.get("matched_rules", []))
                for item in kg_evidence.get("candidate_resources", [])
            ],
            "evidence_sha256": _canonical_sha256(kg_evidence),
        },
        "compiler_contract": {
            "contract_sha256": (
                _canonical_sha256(provider_contract) if provider_contract else ""
            ),
            "resource_types": [
                item.get("type", "")
                for item in provider_contract.get("resource_contracts", [])
            ],
            "dependencies": provider_contract.get("dependency_contracts", []),
        },
        "hcl_kg_input": hcl_kg_input,
        "generation_cost": {
            "retrieval_ms": retrieval_ms,
            "ir": _generation_note(ir_output),
        },
        "_normalized_ir": normalized_graph,
        "_provider_contract": provider_contract,
        "_schema_context": schema.context,
    }


def evaluate_one(row, mode, static_plan, repair_budget=0):
    result = {base + "0": "" for base in RESULT_COLUMN_BASES}
    # Leakage boundary: generation sees only Prompt and public evidence.
    question_prompt = row.get("Prompt", "")
    try:
        generation_prompt, notes = build_generation_prompt(mode, question_prompt)
        normalized_ir = notes.pop("_normalized_ir", graph_ir.empty_graph_ir())
        notes.pop("_provider_contract", None)
        schema_context = notes.pop("_schema_context", "")
        notes["repair"] = local_repair.policy_manifest()
        notes["repair"]["configured_max_steps"] = repair_budget
        notes["repair"]["enabled"] = repair_budget > 0
        notes["repair"]["calls"] = 0
        hcl_output = models.generate_with_metadata(
            COMPILER_SYSTEM_PROMPT,
            generation_prompt,
            stage="hcl",
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
            )
            raw_llm_hcl = extract_hcl(output.text)
            hcl, normalization_diff = normalize_terraform_config_with_diff(
                raw_llm_hcl, static_plan
            )
            (workdir / "main.tf").write_text(hcl, encoding="utf-8")
            repair_used = True
            notes["repair"]["configured_max_steps"] = repair_budget
            notes["repair"]["calls"] = 1
            notes["repair"]["generation"] = _generation_note(output)
            notes["hcl_generation"]["repaired_raw_llm_hcl"] = raw_llm_hcl
            notes["hcl_generation"]["repaired_normalized_hcl"] = hcl
            notes["hcl_generation"]["repair_normalization_diff"] = normalization_diff

        result["LLM Compilable? #0"] = bool(compilable)
        result["LLM Compile Phase Error #0"] = "" if compilable else compile_error
        if not compilable:
            if repair_budget > 0:
                repair_once(compile_error)
                compilable, compile_error = terraform_validate(workdir, static_plan)
                result["LLM Compilable? #0"] = bool(compilable)
                result["LLM Compile Phase Error #0"] = (
                    "" if compilable else compile_error
                )
            if not compilable:
                notes["repair_outcome"] = {
                    "initial_validate": initial_validate,
                    "initial_plan": False,
                    "final_validate": bool(compilable),
                    "final_plan": False,
                    "repair_used": repair_used,
                }
                result["LLM Output #0"] = hcl
                result["LLM Notes #0"] = json.dumps(
                    notes, ensure_ascii=True, sort_keys=True
                )
                return result

        plannable, plan_json, plan_error = terraform_plan_json(workdir, static_plan)
        initial_plan = bool(plannable)
        if not plannable and repair_budget > 0 and not repair_used:
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
    result_path = results_root / mode / model_name / file_name
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
    parser.add_argument("--kg", choices=("full", "paper"), required=True)
    parser.add_argument("--repair", action="store_true")
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
        raise ValueError("IaCForge supports samples=1 only.")
    if len(config_models) != 1:
        raise ValueError("Model config must contain exactly one model name.")
    if args.checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be at least 1.")

    repair_budget = local_repair.configured_max_steps(args.repair)
    mode = experiment_mode(args.kg, repair_budget > 0)
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
        row_result = evaluate_one(row, mode, args.static_plan, repair_budget)
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
