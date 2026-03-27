#!/bin/bash
# Note: no set -euo pipefail — we do explicit error checking to avoid silent
# termination from racing PIDs and transient command failures (B16).

HOOK_INPUT=$(cat)

# Skip hook in sub-agent sessions (they use claude -p, not hook IPC)
if [[ "$AUTO_SKIP_HOOK" == "1" ]]; then
  exit 0
fi

echo "[auto] stop-hook invoked" >&2

# --- Resolve state file via session ID ---
HOOK_SESSION=$(echo "$HOOK_INPUT" | jq -r '.session_id // ""' 2>/dev/null)

if [[ -z "$HOOK_SESSION" ]]; then
  echo "[auto] no session_id in hook input, exiting" >&2
  exit 0
fi

# Fast path: session already registered from a previous invocation
SESSIONS_DIR="$HOME/.auto/sessions"
SESSION_FILE="$SESSIONS_DIR/$HOOK_SESSION"

if [[ -f "$SESSION_FILE" ]]; then
  RUN_DIR=$(cat "$SESSION_FILE")
  STATE_FILE="$RUN_DIR/self.json"
  echo "[auto] session $HOOK_SESSION -> $STATE_FILE" >&2
else
  # First invocation: find our run folder via PID bridge.
  # The CLI wrote ~/.auto/pids/<pid> containing the run dir path.
  # We check each live PID's run folder for an unclaimed self.json.
  STATE_FILE=""
  PIDS_DIR="$HOME/.auto/pids"

  # Poll briefly — CLI may still be writing the PID file
  WAIT_COUNT=0
  MAX_WAIT=50  # 10s
  while [[ $WAIT_COUNT -lt $MAX_WAIT ]]; do
    if [[ -d "$PIDS_DIR" ]]; then
      for PID_FILE in "$PIDS_DIR"/*; do
        [[ -f "$PID_FILE" ]] || continue
        CANDIDATE_PID=$(basename "$PID_FILE")
        # Must be alive
        kill -0 "$CANDIDATE_PID" 2>/dev/null || continue
        CANDIDATE_RUN=$(cat "$PID_FILE")
        CANDIDATE_STATE="$CANDIDATE_RUN/self.json"
        [[ -f "$CANDIDATE_STATE" ]] || continue
        # Must be unclaimed (empty session_id) or already ours
        CANDIDATE_SID=$(jq -r '.session_id // ""' "$CANDIDATE_STATE" 2>/dev/null)
        if [[ -z "$CANDIDATE_SID" ]] || [[ "$CANDIDATE_SID" == "$HOOK_SESSION" ]]; then
          STATE_FILE="$CANDIDATE_STATE"
          RUN_DIR="$CANDIDATE_RUN"
          break 2  # break both loops
        fi
      done
    fi
    sleep 0.2
    WAIT_COUNT=$((WAIT_COUNT + 1))
  done

  if [[ -z "$STATE_FILE" ]]; then
    echo "[auto] no unclaimed run folder found, exiting" >&2
    exit 0
  fi

  # Register for future fast-path lookups and stamp session_id into state
  mkdir -p "$SESSIONS_DIR"
  echo "$RUN_DIR" > "$SESSION_FILE"
  TEMP_REG="${STATE_FILE}.reg.$$"
  jq --arg sid "$HOOK_SESSION" '.session_id = $sid' "$STATE_FILE" > "$TEMP_REG" 2>/dev/null && mv "$TEMP_REG" "$STATE_FILE"
  echo "[auto] registered session $HOOK_SESSION -> $RUN_DIR" >&2
fi

# --- Guard: state file must exist (poll briefly for startup) ---
WAIT_COUNT=0
MAX_WAIT=50  # 10s
while [[ ! -f "$STATE_FILE" ]] && [[ $WAIT_COUNT -lt $MAX_WAIT ]]; do
  sleep 0.2
  WAIT_COUNT=$((WAIT_COUNT + 1))
done
if [[ ! -f "$STATE_FILE" ]]; then
  echo "[auto] state file not found after ${MAX_WAIT} polls, exiting" >&2
  exit 0
fi

# --- Parse state file ---
STATE=$(cat "$STATE_FILE")
STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
PYTHON_PID=$(echo "$STATE" | jq -r '.python_pid // 0' 2>/dev/null)
STEP_NUMBER=$(echo "$STATE" | jq -r '.step_number // 0' 2>/dev/null)

if [[ -z "$STATUS" ]] || [[ "$STATUS" == "null" ]]; then
  echo "[auto] state file has invalid status, bailing" >&2
  exit 0
fi

# --- Python liveness check ---
if [[ "$PYTHON_PID" -gt 0 ]] && ! kill -0 "$PYTHON_PID" 2>/dev/null; then
  echo "[auto] Python process $PYTHON_PID is dead, cleaning up" >&2
  rm -f "$STATE_FILE" "$SESSION_FILE"
  exit 0
fi

# --- Handle "done" immediately ---
if [[ "$STATUS" == "done" ]]; then
  rm -f "$STATE_FILE" "$SESSION_FILE"
  exit 0
fi

# --- Phase 1: Deliver response if Claude just finished a step ---
if [[ "$STATUS" == "running" ]]; then
  echo "[auto] Phase 1: status=running, step=$STEP_NUMBER, delivering response" >&2
  TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)

  LAST_OUTPUT=""
  if [[ -f "$TRANSCRIPT_PATH" ]]; then
    PREV_LINES=$(echo "$STATE" | jq -r '.transcript_lines // -1' 2>/dev/null)

    POLL_ATTEMPTS=0
    MAX_POLL=25  # 5s
    while [[ $POLL_ATTEMPTS -lt $MAX_POLL ]]; do
      TOTAL_LINES=$(grep -c '' "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
      if [[ "$PREV_LINES" -ge 0 ]] && [[ "$TOTAL_LINES" -ge "$PREV_LINES" ]]; then
        LAST_OUTPUT=$(tail -n +"$((PREV_LINES + 1))" "$TRANSCRIPT_PATH" | jq -rs '
          [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
        ' 2>&1) || echo "[auto] Phase 1: jq FAILED parsing transcript" >&2
      else
        LAST_OUTPUT=$(tail -n 200 "$TRANSCRIPT_PATH" | jq -rs '
          [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | join("\n")
        ' 2>&1) || echo "[auto] Phase 1: jq FAILED parsing transcript" >&2
      fi

      [[ -n "$LAST_OUTPUT" ]] && break
      POLL_ATTEMPTS=$((POLL_ATTEMPTS + 1))
      sleep 0.2
    done
  fi

  RESPONSE_LEN=${#LAST_OUTPUT}
  echo "[auto] Phase 1: extracted ${RESPONSE_LEN}b" >&2

  TEMP_FILE="${STATE_FILE}.tmp.$$"
  echo "$STATE" | jq \
    --arg status "responded" \
    --arg response "$LAST_OUTPUT" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.status = $status | .response = $response | .updated_at = $updated_at' \
    > "$TEMP_FILE" 2>/dev/null
  JQ_EXIT=$?
  if [[ $JQ_EXIT -ne 0 ]]; then
    echo "[auto] Phase 1: jq FAILED writing responded state, bailing" >&2
    rm -f "$TEMP_FILE"
    exit 0
  fi
  mv "$TEMP_FILE" "$STATE_FILE"
fi

# --- Phase 2: Wait for next instruction from Python ---
while true; do
  [[ -f "$STATE_FILE" ]] || exit 0

  STATE=$(cat "$STATE_FILE")
  STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
  PYTHON_PID=$(echo "$STATE" | jq -r '.python_pid // 0' 2>/dev/null)

  [[ -z "$STATUS" ]] && exit 0

  if [[ "$PYTHON_PID" -gt 0 ]] && ! kill -0 "$PYTHON_PID" 2>/dev/null; then
    echo "[auto] Phase 2: Python PID $PYTHON_PID is DEAD" >&2
    rm -f "$STATE_FILE" "$SESSION_FILE"
    exit 0
  fi

  if [[ "$STATUS" == "pending" ]]; then
    PENDING_STEP=$(echo "$STATE" | jq -r '.step_number // 0' 2>/dev/null)
    echo "[auto] Phase 2: injecting step=$PENDING_STEP" >&2
    INSTRUCTION=$(echo "$STATE" | jq -r '.instruction // ""' 2>/dev/null)
    SCHEMA=$(echo "$STATE" | jq -r '.schema // "null"' 2>/dev/null)

    PROMPT="$INSTRUCTION"
    if [[ "$SCHEMA" != "null" ]] && [[ "$SCHEMA" != "" ]]; then
      SCHEMA_DESC=$(echo "$SCHEMA" | jq '.' 2>/dev/null)
      PROMPT="${PROMPT}

Respond with a JSON object with these keys and types:
${SCHEMA_DESC}"
    fi

    TRANSCRIPT_PATH_FOR_COUNT=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)
    CURRENT_LINES=0
    [[ -f "$TRANSCRIPT_PATH_FOR_COUNT" ]] && CURRENT_LINES=$(grep -c '' "$TRANSCRIPT_PATH_FOR_COUNT")

    TEMP_FILE="${STATE_FILE}.tmp.$$"
    echo "$STATE" | jq \
      --arg status "running" \
      --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --argjson transcript_lines "$CURRENT_LINES" \
      '.status = $status | .transcript_lines = $transcript_lines | .updated_at = $updated_at' \
      > "$TEMP_FILE" 2>/dev/null && mv "$TEMP_FILE" "$STATE_FILE"

    jq -n --arg reason "$PROMPT" '{"decision": "block", "reason": $reason}'
    exit 0

  elif [[ "$STATUS" == "done" ]] || [[ "$STATUS" == "error" ]]; then
    rm -f "$STATE_FILE" "$SESSION_FILE"
    exit 0
  fi

  sleep 0.2
done
