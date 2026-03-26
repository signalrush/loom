#!/bin/bash
# Note: no set -euo pipefail — we do explicit error checking to avoid silent
# termination from racing PIDs and transient command failures (B16).

HOOK_INPUT=$(cat)
STATE_FILE=".claude/auto-loop.json"
# Log invocation for debuggability (appears in Claude Code's hook debug output)
echo "[auto] stop-hook invoked" >&2

# --- Guard: verify hook CWD is the project root ---
if [[ ! -d ".claude" ]]; then
  echo "[auto] ERROR: .claude/ directory not found in CWD ($(pwd)). Hook CWD may be wrong." >&2
  exit 0
fi

# --- Guard: no state file means no active loop ---
if [[ ! -f "$STATE_FILE" ]]; then
  exit 0
fi

# --- Parse state file ---
STATE=$(cat "$STATE_FILE")
STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
STATE_SESSION=$(echo "$STATE" | jq -r '.session_id // ""' 2>/dev/null)
PYTHON_PID=$(echo "$STATE" | jq -r '.python_pid // 0' 2>/dev/null)
STEP_NUMBER=$(echo "$STATE" | jq -r '.step_number // 0' 2>/dev/null)

# If jq failed (invalid JSON) or status is null, bail cleanly
if [[ -z "$STATUS" ]] || [[ "$STATUS" == "null" ]]; then
  echo "[auto] state file has invalid status, bailing" >&2
  exit 0
fi

# --- Session isolation ---
HOOK_SESSION=$(echo "$HOOK_INPUT" | jq -r '.session_id // ""' 2>/dev/null)
if [[ -n "$STATE_SESSION" ]] && [[ "$STATE_SESSION" != "$HOOK_SESSION" ]]; then
  exit 0
fi

# --- Python liveness check ---
if [[ "$PYTHON_PID" -gt 0 ]] && ! kill -0 "$PYTHON_PID" 2>/dev/null; then
  echo "[auto] Python process $PYTHON_PID is dead, cleaning up" >&2
  rm -f "$STATE_FILE"
  exit 0
fi

# --- Handle "done" immediately ---
if [[ "$STATUS" == "done" ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

# --- Phase 1: Deliver response if Claude just finished a step ---
if [[ "$STATUS" == "running" ]]; then
  TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)

  LAST_OUTPUT=""
  if [[ -f "$TRANSCRIPT_PATH" ]]; then
    PREV_LINES=$(echo "$STATE" | jq -r '.transcript_lines // -1' 2>/dev/null)
    TOTAL_LINES=$(grep -c '' "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
    if [[ "$PREV_LINES" -ge 0 ]] && [[ "$TOTAL_LINES" -ge "$PREV_LINES" ]]; then
      # Normal: read only lines added since the step started
      LAST_OUTPUT=$(tail -n +"$((PREV_LINES + 1))" "$TRANSCRIPT_PATH" | jq -rs '
        [.[] | select(.role == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
      ' 2>/dev/null)
    else
      # Fallback: no line count, or transcript was compacted (shorter than snapshot)
      LAST_OUTPUT=$(tail -n 200 "$TRANSCRIPT_PATH" | jq -rs '
        [.[] | select(.role == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
      ' 2>/dev/null)
    fi
  fi

  # Write response atomically
  TEMP_FILE="${STATE_FILE}.tmp.$$"
  echo "$STATE" | jq \
    --arg status "responded" \
    --arg response "$LAST_OUTPUT" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.status = $status | .response = $response | .updated_at = $updated_at' \
    > "$TEMP_FILE" 2>/dev/null
  JQ_EXIT=$?
  if [[ $JQ_EXIT -ne 0 ]]; then
    echo "[auto] jq failed writing responded state, bailing" >&2
    rm -f "$TEMP_FILE"
    exit 0
  fi
  mv "$TEMP_FILE" "$STATE_FILE"
fi

# --- Phase 2: Wait for next instruction from Python ---
# Polls indefinitely at 200ms intervals. The only exit conditions are:
#   - Python writes "pending" (inject next instruction)
#   - Python writes "done" (loop finished)
#   - Python writes "error" (something went wrong)
#   - Python PID is dead (cleanup)
#   - State file disappears (cleanup)

while true; do
  # Re-read state file
  if [[ ! -f "$STATE_FILE" ]]; then
    exit 0
  fi

  STATE=$(cat "$STATE_FILE")
  STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
  # B7: re-read PYTHON_PID from state file each iteration so we track restarts
  PYTHON_PID=$(echo "$STATE" | jq -r '.python_pid // 0' 2>/dev/null)

  if [[ -z "$STATUS" ]]; then
    echo "[auto] state file became invalid JSON in Phase 2, bailing" >&2
    exit 0
  fi

  # Check Python is still alive (explicit return code check, no set -e)
  if [[ "$PYTHON_PID" -gt 0 ]]; then
    kill -0 "$PYTHON_PID" 2>/dev/null
    if [[ $? -ne 0 ]]; then
      rm -f "$STATE_FILE"
      exit 0
    fi
  fi

  if [[ "$STATUS" == "pending" ]]; then
    INSTRUCTION=$(echo "$STATE" | jq -r '.instruction // ""' 2>/dev/null)
    SCHEMA=$(echo "$STATE" | jq -r '.schema // "null"' 2>/dev/null)

    # Build prompt with schema instructions if needed
    # B13: PROMPT is built as a shell variable; jq --arg handles quoting correctly
    # as long as the variable is double-quoted when passed to jq (which it is below).
    PROMPT="$INSTRUCTION"
    if [[ "$SCHEMA" != "null" ]] && [[ "$SCHEMA" != "" ]]; then
      SCHEMA_DESC=$(echo "$SCHEMA" | jq '.' 2>/dev/null)
      PROMPT="${PROMPT}

Respond with ONLY a JSON object. The keys and their expected types are:
${SCHEMA_DESC}

Replace the type descriptions with actual values. For example, if the schema is {\"name\": \"str\", \"age\": \"int\"}, you would return {\"name\": \"Alice\", \"age\": 30}.
Return ONLY the JSON object, no other text."
    fi

    # Count current transcript lines so Phase 1 knows where the current turn starts
    TRANSCRIPT_PATH_FOR_COUNT=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)
    CURRENT_LINES=0
    if [[ -f "$TRANSCRIPT_PATH_FOR_COUNT" ]]; then
        CURRENT_LINES=$(grep -c '' "$TRANSCRIPT_PATH_FOR_COUNT")
    fi

    # Mark as running atomically
    TEMP_FILE="${STATE_FILE}.tmp.$$"
    echo "$STATE" | jq \
      --arg status "running" \
      --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson transcript_lines "$CURRENT_LINES" \
      '.status = $status | .transcript_lines = $transcript_lines | .updated_at = $updated_at' \
      > "$TEMP_FILE" 2>/dev/null
    JQ_EXIT=$?
    if [[ $JQ_EXIT -ne 0 ]]; then
      echo "[auto] jq failed writing running state, bailing" >&2
      rm -f "$TEMP_FILE"
      exit 0
    fi
    mv "$TEMP_FILE" "$STATE_FILE"

    # Block and inject instruction
    # B13: --arg properly escapes the value; PROMPT is double-quoted here
    jq -n --arg reason "$PROMPT" '{"decision": "block", "reason": $reason}'
    exit 0

  elif [[ "$STATUS" == "done" ]]; then
    rm -f "$STATE_FILE"
    exit 0

  elif [[ "$STATUS" == "error" ]]; then
    rm -f "$STATE_FILE"
    exit 0

  # "starting": Python is alive but program_fn hasn't called step() yet.
  # "responded": Python hasn't consumed the response yet.
  # Both fall through to sleep and re-poll.
  fi

  sleep 0.2
done
