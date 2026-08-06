#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVAL_DIR="$ROOT_DIR/evaluation"
PYTHON="${PYTHON:-python3}"
MODE="${MODE:-full_kg}"
MODEL="${MODEL:-qwen2.5-coder-3b}"
MAX_ROWS="${MAX_ROWS:-458}"
ROW_IDS_FILE="${ROW_IDS_FILE:-}"
RESUME="${RESUME:-1}"
CHECKPOINT_EVERY="${CHECKPOINT_EVERY:-1}"

case "$MODEL" in
  qwen2.5-coder-3b|qianwen2.5-coder-3b)
    CONFIG="$ROOT_DIR/configs/models/qwen2.5-coder-3b.json"
    SERVED_MODEL="qwen2.5-coder-3b"
    OUTPUT_MODEL_DIR="qwen2.5-coder-3b"
    ;;
  qwen2.5-coder-14b)
    CONFIG="$ROOT_DIR/configs/models/qwen2.5-coder-14b.json"
    SERVED_MODEL="qwen2.5-coder-14b"
    OUTPUT_MODEL_DIR="qwen2.5-coder-14b"
    ;;
  *)
    echo "Unknown MODEL: $MODEL" >&2
    exit 2
    ;;
esac

KG_KIND=""
REPAIR_ARG=()
MAX_TOKENS=1536
REPAIR_STEPS_DEFAULT=0
case "$MODE" in
  full_kg)
    KG_KIND=full
    ;;
  full_kg_repair)
    KG_KIND=full
    REPAIR_STEPS_DEFAULT=1
    REPAIR_ARG=(--repair)
    ;;
  paper_kg)
    KG_KIND=paper
    MAX_TOKENS=8192
    ;;
  paper_kg_repair)
    KG_KIND=paper
    MAX_TOKENS=16384
    REPAIR_STEPS_DEFAULT=1
    REPAIR_ARG=(--repair)
    ;;
  *)
    echo "Unknown MODE: $MODE" >&2
    echo "Use full_kg, full_kg_repair, paper_kg, or paper_kg_repair." >&2
    exit 2
    ;;
esac

COMMON_ENV=(
  QWEN_BASE_URL="${QWEN_BASE_URL:-http://127.0.0.1:8000/v1}"
  QWEN_API_KEY="${QWEN_API_KEY:-EMPTY}"
  QWEN_MODEL="${QWEN_MODEL:-$SERVED_MODEL}"
  QWEN_TEMPERATURE="${QWEN_TEMPERATURE:-0.2}"
  QWEN_TOP_P="${QWEN_TOP_P:-0.95}"
  QWEN_IR_MAX_TOKENS="${QWEN_IR_MAX_TOKENS:-$MAX_TOKENS}"
  QWEN_HCL_MAX_TOKENS="${QWEN_HCL_MAX_TOKENS:-$MAX_TOKENS}"
  QWEN_REPAIR_MAX_TOKENS="${QWEN_REPAIR_MAX_TOKENS:-$MAX_TOKENS}"
  QWEN_MAX_RETRIES="${QWEN_MAX_RETRIES:-3}"
  TERRAFORM_PLAN_TIMEOUT_SECONDS="${TERRAFORM_PLAN_TIMEOUT_SECONDS:-100}"
  TERRAFORM_VERSION_CONSTRAINT="${TERRAFORM_VERSION_CONSTRAINT:-~> 1.9.8}"
  AWS_PROVIDER_VERSION_CONSTRAINT="${AWS_PROVIDER_VERSION_CONSTRAINT:-= 5.90.0}"
  VERIGRAPH_MAX_REPAIR_STEPS="${VERIGRAPH_MAX_REPAIR_STEPS:-$REPAIR_STEPS_DEFAULT}"
  IAC_SCHEMA_FILE="$ROOT_DIR/data/schema_grounding/aws-provider-schema.json"
  IAC_PROVIDER_CONTRACT_ROOT="$ROOT_DIR/data/full_kg/provider_kg"
  IAC_KG_PAPER_REPLICATION_ROOT="$ROOT_DIR/data/paper_kg/source"
  IAC_KG_PAPER_CHROMA_DIR="$ROOT_DIR/data/paper_kg/chroma"
  PYTHONUNBUFFERED=1
)

if [[ -n "${IAC_PROVIDER_MIRROR:-}" ]]; then
  COMMON_ENV+=(IAC_PROVIDER_MIRROR="$IAC_PROVIDER_MIRROR")
elif [[ -d "$ROOT_DIR/data/provider_mirror" ]]; then
  COMMON_ENV+=(IAC_PROVIDER_MIRROR="$ROOT_DIR/data/provider_mirror")
fi

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
env "${COMMON_ENV[@]}" EVAL_MODEL_DIR="$OUTPUT_MODEL_DIR" \
  "$PYTHON" eval_verigraph.py \
    --config "$CONFIG" \
    --kg "$KG_KIND" \
    "${REPAIR_ARG[@]}" \
    --static-plan \
    --max-rows "$MAX_ROWS" \
    --log-file "logs/${MODEL}-${MODE}.log" \
    "${EXTRA_ARGS[@]}"
