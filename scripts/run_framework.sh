#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVAL_DIR="$ROOT_DIR/evaluation"

python_has_runtime_deps() {
  [[ -x "$1" ]] && "$1" -c 'import openai, pandas' >/dev/null 2>&1
}

if [[ -n "${PYTHON_BIN:-}" ]]; then
  if ! python_has_runtime_deps "$PYTHON_BIN"; then
    echo "PYTHON_BIN cannot import openai and pandas: $PYTHON_BIN" >&2
    exit 2
  fi
  PYTHON="$PYTHON_BIN"
else
  PYTHON=""
  for candidate in \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/.conda-env/bin/python" \
    "${CONDA_PREFIX:-}/bin/python" \
    "$HOME/miniconda3/envs/iac-eval/bin/python" \
    "$HOME/miniconda3/bin/python" \
    "$(command -v python3 2>/dev/null || true)"; do
    if python_has_runtime_deps "$candidate"; then
      PYTHON="$candidate"
      break
    fi
  done
  if [[ -z "$PYTHON" ]]; then
    echo "No Python interpreter with openai and pandas was found." >&2
    echo "Create .venv/.conda-env or set PYTHON_BIN." >&2
    exit 2
  fi
fi
echo "Using Python: $PYTHON"

MODE="${MODE:-${1:-full}}"
MODEL="${MODEL:-${2:-qwen2.5-coder-3b}}"
MAX_ROWS="${MAX_ROWS:-458}"
ROW_IDS_FILE="${ROW_IDS_FILE:-}"
RESUME="${RESUME:-1}"
CHECKPOINT_EVERY="${CHECKPOINT_EVERY:-1}"

case "$MODEL" in
  qwen2.5-coder-14b) CONFIG="$ROOT_DIR/configs/models/qwen2.5-coder-14b.json"; SERVED_MODEL="qwen2.5-coder-14b"; OUTPUT_MODEL_DIR="qwen2.5-coder-14b" ;;
  qwen2.5-coder-3b|qianwen2.5-coder-3b) CONFIG="$ROOT_DIR/configs/models/qwen2.5-coder-3b.json"; SERVED_MODEL="qwen2.5-coder-3b"; OUTPUT_MODEL_DIR="qwen2.5-coder-3b" ;;
  qwen2.5-coder-32b|qwen2.5-coder-32b-awq) CONFIG="$ROOT_DIR/configs/models/qwen2.5-coder-32b-awq.json"; SERVED_MODEL="qwen2.5-coder-32b"; OUTPUT_MODEL_DIR="qwen2.5-coder-32b-awq" ;;
  mistral-7b-instruct) CONFIG="$ROOT_DIR/configs/models/mistral-7b-instruct.json"; SERVED_MODEL="mistral-7b-instruct"; OUTPUT_MODEL_DIR="mistral-7b-instruct" ;;
  qwen3-14b) CONFIG="$ROOT_DIR/configs/models/qwen3-14b.json"; SERVED_MODEL="qwen3-14b"; OUTPUT_MODEL_DIR="qwen3-14b" ;;
  codellama-13b-instruct) CONFIG="$ROOT_DIR/configs/models/codellama-13b-instruct.json"; SERVED_MODEL="codellama-13b-instruct"; OUTPUT_MODEL_DIR="codellama-13b-instruct" ;;
  qwen3-8b) CONFIG="$ROOT_DIR/configs/models/qwen3-8b.json"; SERVED_MODEL="qwen3-8b"; OUTPUT_MODEL_DIR="qwen3-8b" ;;
  *) echo "Unknown MODEL: $MODEL" >&2; exit 2 ;;
esac

COMMON_ENV=(
  QWEN_BASE_URL="${QWEN_BASE_URL:-http://127.0.0.1:8000/v1}"
  QWEN_API_KEY="${QWEN_API_KEY:-EMPTY}"
  QWEN_MODEL="${QWEN_MODEL:-$SERVED_MODEL}"
  QWEN_MAX_TOKENS="${QWEN_MAX_TOKENS:-2048}"
  QWEN_TEMPERATURE="${QWEN_TEMPERATURE:-0}"
  QWEN_TOP_P="${QWEN_TOP_P:-1}"
  QWEN_IR_TEMPERATURE="${QWEN_IR_TEMPERATURE:-0}"
  QWEN_IR_TOP_P="${QWEN_IR_TOP_P:-1}"
  QWEN_IR_MAX_TOKENS="${QWEN_IR_MAX_TOKENS:-1536}"
  QWEN_IR_GUIDED_JSON="${QWEN_IR_GUIDED_JSON:-1}"
  QWEN_HCL_TEMPERATURE="${QWEN_HCL_TEMPERATURE:-0}"
  QWEN_HCL_TOP_P="${QWEN_HCL_TOP_P:-1}"
  QWEN_MAX_RETRIES="${QWEN_MAX_RETRIES:-3}"
  TERRAFORM_PLAN_TIMEOUT_SECONDS="${TERRAFORM_PLAN_TIMEOUT_SECONDS:-100}"
  TERRAFORM_VERSION_CONSTRAINT="${TERRAFORM_VERSION_CONSTRAINT:-~> 1.9.8}"
  AWS_PROVIDER_VERSION_CONSTRAINT="${AWS_PROVIDER_VERSION_CONSTRAINT:-= 5.90.0}"
  IAC_SCHEMA_FILE="$ROOT_DIR/data/schema_grounding/aws-provider-schema.json"
  IAC_KG_REPLICATION_ROOT="$ROOT_DIR/data/leakfree_multigranular_kg/terraform_aws_5.90.0_public_kg"
  IAC_PROVIDER_CONTRACT_ROOT="$ROOT_DIR/data/leakfree_multigranular_kg/terraform_aws_5.90.0_public_kg"
  IAC_OFFLINE_PROVIDER_CONTRACT_CACHE="$ROOT_DIR/data/leakfree_multigranular_kg/offline_retrieval/provider_contract_full458.jsonl"
  IAC_ALLOW_ONLINE_KG_RETRIEVAL="${IAC_ALLOW_ONLINE_KG_RETRIEVAL:-0}"
  IAC_USE_HCL_SKELETON="${IAC_USE_HCL_SKELETON:-0}"
  PYTHONUNBUFFERED=1
)

if [[ -n "${IAC_PROVIDER_MIRROR:-}" ]]; then
  COMMON_ENV+=(IAC_PROVIDER_MIRROR="$IAC_PROVIDER_MIRROR")
elif [[ -d "$ROOT_DIR/data/provider_mirror" ]]; then
  COMMON_ENV+=(IAC_PROVIDER_MIRROR="$ROOT_DIR/data/provider_mirror")
fi

ENHANCE_STRAT=""
OUTPUT_SUFFIX=""
case "$MODE" in
  baseline)
    ENHANCE_STRAT=""
    OUTPUT_SUFFIX="-baseline-full${MAX_ROWS}"
    ;;
  ir_schema)
    ENHANCE_STRAT="VeriGraph"
    COMMON_ENV+=(VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1 VERIGRAPH_IR_KIND=graph)
    OUTPUT_SUFFIX="-graphir-schema-full${MAX_ROWS}"
    ;;
  full)
    ENHANCE_STRAT="FullKGVeriGraph"
    COMMON_ENV+=(
      VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1
      VERIGRAPH_IR_KIND=graph
    )
    OUTPUT_SUFFIX="-graphir-schema-iacforgekg-full${MAX_ROWS}"
    ;;
  planner_kg)
    ENHANCE_STRAT="FullKGVeriGraph"
    COMMON_ENV+=(IACFORGE_MODE=planner_kg VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1)
    OUTPUT_SUFFIX="-planner-kg-ablation-full${MAX_ROWS}"
    ;;
  compiler_kg)
    ENHANCE_STRAT="FullKGVeriGraph"
    COMMON_ENV+=(IACFORGE_MODE=compiler_kg VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1)
    OUTPUT_SUFFIX="-compiler-kg-ablation-full${MAX_ROWS}"
    ;;
  full_strict)
    ENHANCE_STRAT="FullKGVeriGraph"
    COMMON_ENV+=(IACFORGE_MODE=full_strict VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1)
    OUTPUT_SUFFIX="-full-strict-full${MAX_ROWS}"
    ;;
  full_repair1)
    ENHANCE_STRAT="FullKGVeriGraph"
    COMMON_ENV+=(IACFORGE_MODE=full_repair1 VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=1)
    OUTPUT_SUFFIX="-full-repair1-full${MAX_ROWS}"
    ;;
  ir_only)
    ENHANCE_STRAT="VeriGraph"
    COMMON_ENV+=(VERIGRAPH_PROMPT_DERIVED_SCHEMA_GROUNDING=0 VERIGRAPH_IR_KIND=graph)
    OUTPUT_SUFFIX="-graphir-only-full${MAX_ROWS}"
    ;;
  *) echo "Unknown MODE: $MODE" >&2; exit 2 ;;
esac

EXTRA_ARGS=(--checkpoint-every "$CHECKPOINT_EVERY")
if [[ -n "$ROW_IDS_FILE" ]]; then
  EXTRA_ARGS+=(--row-ids-file "$ROW_IDS_FILE")
fi
if [[ "$RESUME" == "0" ]]; then
  EXTRA_ARGS+=(--no-resume)
else
  EXTRA_ARGS+=(--resume)
fi

cd "$EVAL_DIR"
env "${COMMON_ENV[@]}" EVAL_OUTPUT_SUFFIX="${EVAL_OUTPUT_SUFFIX:-$OUTPUT_SUFFIX}" EVAL_MODEL_DIR="$OUTPUT_MODEL_DIR" \
  "$PYTHON" eval_verigraph.py \
    --config "$CONFIG" \
    --enhance-strat "$ENHANCE_STRAT" \
    --static-plan \
    --max-rows "$MAX_ROWS" \
    --log-file "logs/${MODEL}-${MODE}.log" \
    "${EXTRA_ARGS[@]}"
