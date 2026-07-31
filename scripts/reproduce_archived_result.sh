#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${MODE:-${1:-full}}"
MODEL="${MODEL:-${2:-qwen2.5-coder-3b}}"
MAX_ROWS="${MAX_ROWS:-458}"
RUN_TAG="${RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"

case "$MODE:$MODEL" in
  baseline:qwen2.5-coder-14b) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  baseline:qwen2.5-coder-3b|baseline:qianwen2.5-coder-3b) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  baseline:qwen2.5-coder-32b-awq|baseline:qwen2.5-coder-32b) SUFFIX="-baseline-compilemetric-full458-awq-norepair-v1" ;;
  baseline:mistral-7b-instruct) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  baseline:qwen3-14b) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  baseline:codellama-13b-instruct) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  baseline:qwen3-8b) SUFFIX="-baseline-compilemetric-full458-v1" ;;
  ir_schema:qwen2.5-coder-14b) SUFFIX="-graphir-promptschema-norepair-compilemetric-full458-v1" ;;
  ir_schema:qwen2.5-coder-3b|ir_schema:qianwen2.5-coder-3b) SUFFIX="-graphir-promptschema-norepair-compilemetric-full458-v1" ;;
  ir_schema:qwen2.5-coder-32b-awq|ir_schema:qwen2.5-coder-32b) SUFFIX="-graphir-promptschema-norepair-compilemetric-full458-awq-v1" ;;
  ir_schema:mistral-7b-instruct) SUFFIX="-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1" ;;
  ir_schema:qwen3-14b) SUFFIX="-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1" ;;
  ir_schema:codellama-13b-instruct) SUFFIX="-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1" ;;
  ir_schema:qwen3-8b) SUFFIX="-graphir-promptschema-norepair-promptonly-compilemetric-full458-v1" ;;
  full:qwen2.5-coder-14b) SUFFIX="-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v1-32k-maxtok1536-20260727" ;;
  full:qwen2.5-coder-3b|full:qianwen2.5-coder-3b) SUFFIX="-multigranularkg-both-graphir-promptschema-norepair-promptonly-full458-v2-32k-maxtok1536-20260727" ;;
  *) echo "No archived result mapping for MODE=$MODE MODEL=$MODEL" >&2; exit 2 ;;
esac

MODE="$MODE" MODEL="$MODEL" MAX_ROWS="$MAX_ROWS" EVAL_OUTPUT_SUFFIX="$SUFFIX" \
  EVAL_RESULTS_ROOT="${EVAL_RESULTS_ROOT:-$ROOT_DIR/results/reruns/$RUN_TAG}" \
  RESUME="${RESUME:-0}" \
  "$ROOT_DIR/scripts/run_framework.sh"
