#!/bin/bash
# Note: no set -euo pipefail — we do explicit error checking to avoid silent
# termination from racing PIDs and transient command failures (B16).

HOOK_INPUT=$(cat)

# Skip hook in sub-agent sessions (they use claude -p, not hook IPC)
if [[ "$AUTO_SKIP_HOOK" == "1" ]]; then
  exit 0
fi

STATE_FILE="$HOME/.auto/latest/self.json"
# Log invocation for debuggability (appears in Claude Code's hook debug output)
echo "[auto] stop-hook invoked" >&2

# --- Guard: state file must exist ---
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
  echo "[auto] Phase 1: status=running, step=$STEP_NUMBER, delivering response" >&2
  TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)
  echo "[auto] Phase 1: transcript=$TRANSCRIPT_PATH" >&2

  LAST_OUTPUT=""
  if [[ -f "$TRANSCRIPT_PATH" ]]; then
    PREV_LINES=$(echo "$STATE" | jq -r '.transcript_lines // -1' 2>/dev/null)

    # Poll for the assistant text to appear in the transcript.  Claude Code may
    # not have flushed the final assistant message to the JSONL file by the time
    # the stop hook fires (race documented in test_partial_json_last_line).
    # We retry up to MAX_POLL times at 200ms intervals before giving up.
    POLL_ATTEMPTS=0
    MAX_POLL=25  # 25 × 200ms = 5s
    while [[ $POLL_ATTEMPTS -lt $MAX_POLL ]]; do
      TOTAL_LINES=$(grep -c '' "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
      if [[ "$PREV_LINES" -ge 0 ]] && [[ "$TOTAL_LINES" -ge "$PREV_LINES" ]]; then
        LAST_OUTPUT=$(tail -n +"$((PREV_LINES + 1))" "$TRANSCRIPT_PATH" | jq -rs '
          [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
        ' 2>&1) || echo "[auto] Phase 1: jq FAILED parsing transcript (prev_lines=$PREV_LINES)" >&2
      else
        LAST_OUTPUT=$(tail -n 200 "$TRANSCRIPT_PATH" | jq -rs '
          [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
        ' 2>&1) || echo "[auto] Phase 1: jq FAILED parsing transcript (tail -200)" >&2
      fi

      # If we got non-empty output, we're done
      if [[ -n "$LAST_OUTPUT" ]]; then
        break
      fi

      POLL_ATTEMPTS=$((POLL_ATTEMPTS + 1))
      sleep 0.2
    done
    if [[ $POLL_ATTEMPTS -ge $MAX_POLL ]]; then
      echo "[auto] Phase 1: transcript text still empty after ${MAX_POLL} polls" >&2
    fi
  else
    echo "[auto] Phase 1: transcript file not found: $TRANSCRIPT_PATH" >&2
  fi

  RESPONSE_LEN=${#LAST_OUTPUT}
  echo "[auto] Phase 1: extracted response (${RESPONSE_LEN} bytes), first 100: ${LAST_OUTPUT:0:100}" >&2

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
    echo "[auto] Phase 1: jq FAILED (exit=$JQ_EXIT) writing responded state, bailing" >&2
    rm -f "$TEMP_FILE"
    exit 0
  fi
  mv "$TEMP_FILE" "$STATE_FILE"
  echo "[auto] Phase 1: wrote responded (step=$STEP_NUMBER, ${RESPONSE_LEN}b)" >&2
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
      echo "[auto] Phase 2: Python PID $PYTHON_PID is DEAD, cleaning up" >&2
      rm -f "$STATE_FILE"
      exit 0
    fi
  fi

  if [[ "$STATUS" == "pending" ]]; then
    PENDING_STEP=$(echo "$STATE" | jq -r '.step_number // 0' 2>/dev/null)
    echo "[auto] Phase 2: found pending step=$PENDING_STEP, injecting" >&2
    INSTRUCTION=$(echo "$STATE" | jq -r '.instruction // ""' 2>/dev/null)
    SCHEMA=$(echo "$STATE" | jq -r '.schema // "null"' 2>/dev/null)

    # Build prompt with schema instructions if needed
    # B13: PROMPT is built as a shell variable; jq --arg handles quoting correctly
    # as long as the variable is double-quoted when passed to jq (which it is below).
    PROMPT="$INSTRUCTION"
    if [[ "$SCHEMA" != "null" ]] && [[ "$SCHEMA" != "" ]]; then
      SCHEMA_DESC=$(echo "$SCHEMA" | jq '.' 2>/dev/null)
      PROMPT="${PROMPT}

Respond with a JSON object with these keys and types:
${SCHEMA_DESC}"
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
      echo "[auto] Phase 2: jq FAILED (exit=$JQ_EXIT) writing running state, bailing" >&2
      rm -f "$TEMP_FILE"
      exit 0
    fi
    mv "$TEMP_FILE" "$STATE_FILE"
    echo "[auto] Phase 2: wrote running (step=$PENDING_STEP, tl=$CURRENT_LINES), injecting prompt (${#PROMPT} chars)" >&2

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
