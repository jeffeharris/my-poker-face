#!/usr/bin/env bash
# Ralph Wiggum — Autonomous Triage Agent
# "Me fail code? That's unpossible!"
#
# Usage: ./scripts/ralph-wiggum.sh
# Run inside the ralph-wiggum Docker container after authenticating Claude.

set -uo pipefail
# Note: -e intentionally omitted. The claude CLI can throw transient errors
# (e.g., "No messages returned") that are not task failures. The loop handles
# errors via if/else and retry logic instead of exiting the script.

MAX_RETRIES=3
RETRY_DELAY=30

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROMPT_FILE="$PROJECT_DIR/docs/ralph/prompt.md"
PLAN_FILE="$PROJECT_DIR/docs/ralph/implementation_plan.md"
LOG_DIR="$PROJECT_DIR/docs/ralph/logs"

mkdir -p "$LOG_DIR"

# Verify prompt file exists
if [ ! -f "$PROMPT_FILE" ]; then
    echo "ERROR: prompt.md not found at $PROMPT_FILE"
    exit 1
fi

if [ ! -f "$PLAN_FILE" ]; then
    echo "ERROR: implementation_plan.md not found at $PLAN_FILE"
    exit 1
fi

echo "=========================================="
echo "  Ralph Wiggum — Autonomous Triage Agent"
echo "=========================================="
echo "Project: $PROJECT_DIR"
echo "Prompt:  $PROMPT_FILE"
echo "Plan:    $PLAN_FILE"
echo "Logs:    $LOG_DIR"
echo ""

TASK_NUM=0
COMPLETED=0
FAILED=0

while true; do
    TASK_NUM=$((TASK_NUM + 1))
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    LOG_FILE="$LOG_DIR/task_${TASK_NUM}_${TIMESTAMP}.log"

    # Check if all tasks are done
    REMAINING=$(grep -c '^\- \[ \]' "$PLAN_FILE" || true)
    if [ "$REMAINING" -eq 0 ]; then
        echo ""
        echo "=========================================="
        echo "  All tasks checked off! Ralph is done."
        echo "  Completed: $COMPLETED  Failed: $FAILED"
        echo "=========================================="
        break
    fi

    # Show current task
    NEXT_TASK=$(grep -m1 '^\- \[ \]' "$PLAN_FILE" | sed 's/- \[ \] //')
    echo ""
    echo "=== Task #${TASK_NUM} — $(date) ==="
    echo "Next: $NEXT_TASK"
    echo "Remaining: $REMAINING tasks"
    echo "Log: $LOG_FILE"
    echo ""

    # Run Claude headless with the prompt (with retry on transient errors)
    ATTEMPT=0
    TASK_SUCCEEDED=false
    while [ "$ATTEMPT" -lt "$MAX_RETRIES" ]; do
        ATTEMPT=$((ATTEMPT + 1))
        if [ "$ATTEMPT" -gt 1 ]; then
            echo "--- Retry $ATTEMPT/$MAX_RETRIES after ${RETRY_DELAY}s ---"
            sleep "$RETRY_DELAY"
        fi

        if claude -p "$(cat "$PROMPT_FILE")" \
            --dangerously-skip-permissions \
            --max-turns 50 \
            2>&1 | tee "$LOG_FILE"; then
            TASK_SUCCEEDED=true
            break
        fi

        echo "--- Attempt $ATTEMPT failed (exit code $?) ---"
    done

    if [ "$TASK_SUCCEEDED" = true ]; then
        echo "=== Task #${TASK_NUM} completed ==="
        COMPLETED=$((COMPLETED + 1))
    else
        echo "=== Task #${TASK_NUM} failed after $MAX_RETRIES attempts ==="
        FAILED=$((FAILED + 1))
    fi

    echo "Score: $COMPLETED completed, $FAILED failed, $REMAINING remaining"

    # Brief pause between tasks
    sleep 10
done

echo ""
echo "Ralph Wiggum finished. $TASK_NUM tasks attempted ($COMPLETED completed, $FAILED failed)."
