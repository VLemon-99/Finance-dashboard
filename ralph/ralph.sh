#!/usr/bin/env bash
# Ralph loop: runs one fresh Claude Code session per round until the feature
# is complete or the round limit is reached.
#
# Usage (from the repo's top folder):
#   ./ralph/ralph.sh ralph/tasks/F1-csv-upload.json        # default: 15 rounds
#   ./ralph/ralph.sh ralph/tasks/F1-csv-upload.json 25     # custom round limit
#   ./ralph/ralph.sh ralph/tasks/IMPROVE-2026-11-08.json 12 ralph/PROMPT_improve.md "Mode: safe. Max: 5. Focus: all"
#                                                          # other prompt file + extra settings
#
# Each round starts with a completely fresh context; memory lives in the task
# file, the progress log and git history. Permissions come from
# .claude/settings.json: anything not allowed there is refused, never prompted.
# Prefer running this inside a disposable environment (cloud session / VM),
# not on a computer with personal files.

set -uo pipefail

TASK_FILE="${1:-}"
MAX_ROUNDS="${2:-15}"
PROMPT_FILE="${3:-ralph/PROMPT_build.md}"
EXTRA="${4:-}"

if [[ -z "$TASK_FILE" ]]; then
  echo "Usage: ./ralph/ralph.sh ralph/tasks/<name>.json [max-rounds] [prompt-file] [extra settings]"
  exit 1
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Prompt file not found: '${PROMPT_FILE}'"
  exit 1
fi

# Build loops need an existing task file; the improvement loop creates its own.
if [[ "$PROMPT_FILE" == "ralph/PROMPT_build.md" && ! -f "$TASK_FILE" ]]; then
  echo "Task file not found: '${TASK_FILE}'"
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code ('claude') is not installed or not on your PATH."
  exit 1
fi

mkdir -p ralph/logs
NAME="$(basename "$TASK_FILE" .json)"
LOG="ralph/logs/${NAME}-$(date +%Y%m%d-%H%M).log"
ROUND_OUT="$(mktemp)"
trap 'rm -f "$ROUND_OUT"' EXIT

echo "Ralph loop for ${TASK_FILE} — up to ${MAX_ROUNDS} rounds. Log: ${LOG}"

for ((i = 1; i <= MAX_ROUNDS; i++)); do
  echo "=== Round ${i}/${MAX_ROUNDS} — $(date '+%Y-%m-%d %H:%M') ===" | tee -a "$LOG"
  START=$(date +%s)

  claude -p "Follow ${PROMPT_FILE} exactly. Task file: ${TASK_FILE}. ${EXTRA}" \
    --permission-mode acceptEdits 2>&1 | tee "$ROUND_OUT"
  cat "$ROUND_OUT" >> "$LOG"

  if grep -q "<promise>RALPH_COMPLETE</promise>" "$ROUND_OUT"; then
    echo "Done after ${i} rounds. Check the pull request and any blocked stories in ${TASK_FILE}." | tee -a "$LOG"
    exit 0
  fi

  # A round that ends within seconds usually means a usage limit or an error,
  # not real work — stop instead of burning through the remaining rounds.
  if (( $(date +%s) - START < 20 )); then
    echo "Round ended too quickly (usage limit or error?). Stopping. See ${LOG}." | tee -a "$LOG"
    exit 2
  fi

  sleep 5
done

echo "Reached the round limit (${MAX_ROUNDS}). Check ${TASK_FILE} and the progress log." | tee -a "$LOG"
exit 1
