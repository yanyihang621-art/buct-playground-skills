#!/bin/sh
# Run each phase from a fresh shell. Resolve the Python scripts beside this file.
set -eu
scripts=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
input=${METHODS_INPUT_DIR:-/input}
work=${METHODS_WORK_DIR:-/app/work}
output=${METHODS_OUTPUT_DIR:-/app/output}
case_file=${METHODS_CASE_FILE:-$input/case.json}
python=${METHODS_PYTHON:-python3}
phase=${1:-}
if [ "$#" -gt 0 ]; then shift; fi

case "$phase" in
  prepare)
    "$python" "$scripts/extract_experiment_facts.py" --input-dir "$input" --case "$case_file" \
      --out-facts "$work/methods/experiment_facts.json" --out-params "$work/methods/parameter_table.json"
    "$python" "$scripts/read_evidence.py" --facts "$work/methods/experiment_facts.json" "$@"
    ;;
  publish)
    if [ ! -s "$work/methods/agent_draft.md" ]; then
      echo "Write the case manuscript to $work/methods/agent_draft.md before publishing." >&2
      exit 2
    fi
    if [ -f "$work/methods/review.json" ]; then set -- --review-file "$work/methods/review.json" "$@"; fi
    if [ -f "$work/methods/contract_values.json" ]; then set -- --contract-values "$work/methods/contract_values.json" "$@"; fi
    "$python" "$scripts/build_methods_result.py" --input-dir "$input" --case "$case_file" \
      --work-dir "$work" --output-dir "$output" --draft-file "$work/methods/agent_draft.md" \
      --allow-partial "$@"
    ;;
  *)
    echo "Usage: sh harbor_methods.sh prepare [evidence filters] | publish [output options]" >&2
    exit 2
    ;;
esac
