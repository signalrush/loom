# Stop Hook Backend Design

## Executive Summary

Replace both existing backends (OpenCode HTTP in `step.py`, Agent SDK in `step_claude.py`) with a single backend that runs auto programs **inside a live Claude Code TUI session** using stop hooks. The Python program and Claude Code communicate through a JSON state file using file-based IPC. A bash stop hook intercepts Claude's turn endings and either injects the next instruction from the Python program or blocks waiting for one.

This design eliminates the need for a separate server process (OpenCode `serve`) or headless subprocess (Agent SDK). The user sees everything Claude does in their normal TUI. The auto program is a sidecar process that drives the TUI via file coordination.

### Why this is better

| Property | OpenCode HTTP | Agent SDK | Stop Hook (new) |
|---|---|---|---|
| Requires running server | Yes (`opencode serve`) | No | No |
| Visible in TUI | Yes (same session) | No (headless) | Yes (same session) |
| Extra dependencies | httpx, opencode | claude-agent-sdk | None (bash + jq) |
| Works with Claude Code | Via opencode proxy | Via SDK subprocess | Native |
| Session management | HTTP API | SDK handles it | Built-in (same session) |

---

## Post-Implementation Updates

The following changes were made during implementation and 4 rounds of adversarial bug fixing:

1. **No timeouts**: Both the hook's 300s timeout and Python's 600s timeout were removed. Polling is infinite — exits only on status change, dead PID, or file disappearance.
2. **`set -euo pipefail` removed**: The hook uses explicit error checking instead.
3. **`settings.local.json` not `hooks.json`**: Claude Code reads project hooks from settings.local.json.
4. **`except`/`else` not `finally`**: `run_program` writes `error` on crash, `done` on success.
5. **Transcript extraction**: Uses `tail -n 200 | jq -rs` with role filtering in jq, not `grep | tail -n 100 | jq`.
6. **SIGTERM handler**: Converts SIGTERM to SystemExit so cleanup runs.
7. **PYTHONUNBUFFERED=1**: Added to subprocess env for immediate log output.
8. **`flush=True`**: Added to all print() calls.
9. **Orphaned temp file cleanup**: `_write_state` cleans `.auto-loop-*.tmp` files on each write.
10. **`STATUS="null"` guard**: Hook checks for both empty and literal "null" status.

---

## Architecture Overview

```
 +-------------------+        .claude/auto-loop.json        +-------------------+
 |                   |  <-- writes instruction (pending) --  |                   |
 |   Claude Code     |                                       |  Python program   |
 |   TUI session     |  -- writes response (responded) -->   |  (sidecar)        |
 |                   |                                       |                   |
 +--------+----------+                                       +---------+---------+
          |                                                            |
          | stop hook fires                                            | polls file
          | on each turn end                                           | for state
          |                                                            | changes
 +--------+----------+                                                 |
 |  stop-hook.sh     |  reads/writes .claude/auto-loop.json           |
 |  (bash script)    |  reads transcript JSONL for responses          |
 +-------------------+                                                 |
                                                                       |
                                  auto.log  <----- stdout/stderr ------+
                              auto-state.json <--- user program state --+
```

### Data Flow (steady state, steps 2+)

```
  Python step()       State File           Stop Hook           Claude TUI
  -----------         ----------           ---------           ----------
       |                  |                    |                    |
       | write pending    |                    |                    |
       |----------------->|                    |                    |
       |                  |                    |  (Claude finishes) |
       |                  |                    |<-------------------|
       |                  |  read state        |                    |
       |                  |<-------------------|                    |
       |                  |  read transcript   |                    |
       |                  |  for response      |                    |
       |                  |                    |                    |
       |                  |  write responded   |                    |
       |                  |<-------------------|                    |
       | poll, read resp  |                    |                    |
       |<-----------------|                    |                    |
       |                  |                    |                    |
       | (python logic)   |                    |  (hook is still    |
       |                  |                    |   blocking, waiting)|
       |                  |                    |                    |
       | write pending    |                    |                    |
       |----------------->|                    |                    |
       |                  |  read pending      |                    |
       |                  |<-------------------|                    |
       |                  |  write running     |                    |
       |                  |<-------------------|                    |
       |                  |                    | return block+reason|
       |                  |                    |------------------->|
       |                  |                    |                    |
       |                  |                    |   (Claude works)   |
```

---

## 1. State File Format

**Path:** `.claude/auto-loop.json` (project-scoped, gitignored -- add `.claude/auto-loop.json` explicitly to `.gitignore`, not the whole `.claude/` directory, since `.claude/` often contains committed files like `CLAUDE.md` and `settings.json`)

### Schema

```json
{
  "status": "starting | pending | running | responded | done | error",
  "session_id": "uuid-of-claude-session",
  "step_number": 1,
  "instruction": "the natural language instruction",
  "schema": {"key": "type_description"} | null,
  "response": "Claude's text response" | null,
  "error": "error message" | null,
  "python_pid": 12345,
  "cwd": "/absolute/path/to/project/root",
  "updated_at": "2026-03-25T10:00:00Z"
}
```

### Status transitions

```
                          Python writes                Python writes
  (file created) ------> starting ------> pending -------> running -------> responded
                          (heartbeat,       ^               (hook takes     (hook writes
                           before           |                instruction)    response)
                           program_fn)      |                                  |
                                            +--- Python reads response, -------+
                                                 processes, writes next
                                                 pending (or done)

  At any point:
    Python writes "done"  --> hook exits 0, Claude stops
    Python crashes        --> hook detects stale PID, exits 0
    Error                 --> hook writes "error", Python reads and raises
```

### Field details

- **status** (`str`): The FSM state. Python writes `starting` (heartbeat before `program_fn`), `pending`, and `done`. The hook writes `running`, `responded`, and `error`. `starting` signals that the Python process is alive but `program_fn` has not yet called `step()`.
- **session_id** (`str`): Set once during setup. Used by the hook for session isolation -- if the hook fires in a different session, it exits 0.
- **step_number** (`int`): Monotonically increasing. Python increments on each `step()` call. Used by Python's `_wait_for_response` to match the correct `responded` status to the current step.
- **instruction** (`str`): The natural language instruction. Set by Python when status is `pending`. The hook reads it and passes it as the `reason` field in the block response.
- **schema** (`dict|null`): If set, the schema descriptor for structured output. The hook appends JSON formatting instructions to the instruction before passing to Claude. The hook also attempts to extract JSON from the response before writing it back.
- **response** (`str|null`): Claude's response text. Written by the hook after reading the transcript. If schema was provided and JSON extraction succeeds, this contains the JSON string. Otherwise raw text.
- **error** (`str|null`): Error message if something went wrong in the hook.
- **python_pid** (`int`): PID of the Python process. The hook checks if this PID is alive. If not, it cleans up and exits 0.
- **cwd** (`str`): Absolute path to the project root at the time Python started. Recorded by `_start_program` and used to resolve the state file path correctly regardless of the subprocess's working directory.
- **updated_at** (`str`): ISO 8601 timestamp of last write. Used for debugging and staleness detection.

---

## 2. Stop Hook Script

**Path:** `src/auto/hooks/stop-hook.sh`

This is the core of the system. It fires every time Claude finishes a turn. It reads the state file, extracts Claude's response from the transcript, writes the response back, waits for Python to produce the next instruction, and then either blocks (injecting the next instruction) or exits 0 (letting Claude stop).

### Hook CWD note

Claude Code sets the hook subprocess's CWD to the project root (verified against Ralph Loop's identical pattern). The hook uses `STATE_FILE=".claude/auto-loop.json"` as a relative path, which resolves correctly because Claude Code sets the hook's working directory to the project root — the same directory that contains `.claude/`.

To guard against unexpected CWD misconfiguration, the hook checks for the `.claude/` directory at startup:

```bash
if [[ ! -d ".claude" ]]; then
  echo "[auto] ERROR: .claude/ directory not found in CWD ($(pwd)). Hook CWD may be wrong." >&2
  exit 0
fi
```

If `.claude/` is missing in the hook's CWD, this produces a visible diagnostic in Claude Code's hook debug output instead of silently failing when the state file is not found.

### Hook timeout note

The hook's internal timeout is 5 minutes (`TIMEOUT=300`). Claude Code has a hard timeout for hook subprocess execution. The hook's internal 5-minute timeout **must be less than** Claude Code's configured hook timeout, otherwise Claude Code will kill the hook process before it can time out cleanly. The default Claude Code hook timeout value should be verified against Claude Code documentation or source for the installed version. Do not assume it is exactly 10 minutes. At the top of the hook script, the configured timeout is logged so it appears in Claude Code's hook debug output.

If Claude Code kills the hook before the internal timeout fires, the state file remains at `running` (if mid-turn) or `responded` (if past Phase 1). Python's `_wait_for_response` will timeout after `RESPONSE_TIMEOUT` seconds and raise `RuntimeError`.

### Pseudocode

```
read HOOK_INPUT from stdin (JSON with session_id, transcript_path, etc.)
STATE_FILE = ".claude/auto-loop.json"

# Log configured timeout for debuggability
log "[auto] hook invoked (internal timeout: ${TIMEOUT}s)"

if STATE_FILE does not exist:
    exit 0  # no active auto loop

read state from STATE_FILE
parse session_id, status, python_pid, step_number, instruction, schema

# Session isolation
if state.session_id != hook_input.session_id:
    exit 0

# Check Python is alive
if not kill -0 python_pid:
    rm STATE_FILE
    exit 0

# --- Phase 1: Deliver response from this turn ---

if status == "running":
    # Claude just finished processing an instruction
    extract last assistant text from transcript JSONL

    if schema is not null:
        append JSON extraction hint (the hook does NOT parse JSON itself,
        it just passes raw text; Python does the parsing)

    write to STATE_FILE: {status: "responded", response: <extracted_text>, ...}

    # Fall through to Phase 2

elif status == "pending":
    # First invocation: the hook fires for the bootstrap turn,
    # and Python already has a pending instruction.
    # Skip response delivery, go straight to Phase 2.
    pass

elif status == "starting":
    # Python is alive but program_fn hasn't called step() yet.
    # Skip response delivery, fall through to Phase 2 and poll
    # until status becomes "pending" (or "done"/"error").
    pass

elif status == "responded":
    # Python hasn't consumed the response yet. Wait.
    # (This shouldn't normally happen because the hook only fires
    # after Claude finishes, and Claude only runs when the hook
    # returned "block" on a previous invocation.)
    pass

elif status == "done":
    rm STATE_FILE
    exit 0

else:
    # Unknown status, bail
    exit 0

# --- Phase 2: Wait for next instruction ---

TIMEOUT=300  # 5 minutes -- must be less than Claude Code's hard hook timeout
POLL_INTERVAL_DS=2  # 200ms expressed as deciseconds (integer, no bc needed)

for ELAPSED_DS from 0 to TIMEOUT*10 (deciseconds):
    re-read STATE_FILE  # wrapped in set +e / set -e guard

    if status == "pending":
        instruction = state.instruction
        schema = state.schema

        # Build prompt
        prompt = instruction
        if schema:
            prompt += "\n\nRespond with ONLY a JSON object..."

        # Mark as running
        write to STATE_FILE: {status: "running", ...}

        # Inject the instruction
        output JSON: {"decision": "block", "reason": prompt}
        exit 0

    elif status == "done":
        rm STATE_FILE
        exit 0

    elif status == "error":
        rm STATE_FILE
        exit 0

    sleep 0.2
    ELAPSED_DS=$(( ELAPSED_DS + POLL_INTERVAL_DS ))

# Timeout reached -- Python is too slow or stuck
write to STATE_FILE: {status: "error", error: "hook timeout waiting for next instruction"}
exit 0  # let Claude stop
```

### Key implementation details

**Hook input JSON schema.** Claude Code pipes a JSON object to the hook's stdin. The known fields are:

```json
{
  "session_id": "uuid-of-the-current-session",
  "transcript_path": "/absolute/path/to/transcript.jsonl",
  "cwd": "/absolute/path/to/project/root",
  "hook_event_name": "Stop",
  "stop_hook_active": true,
  "permission_mode": "default",
  "last_assistant_message": "..."
}
```

The hook uses `session_id` for session isolation and `transcript_path` for response extraction. Remaining fields are available but not used by this hook.

**Transcript parsing.** Identical pattern to Ralph Loop. The transcript is JSONL. Each line is a message. We grep for `"role":"assistant"`, take the last 100 lines, use jq to extract the final text block.

**Atomic file writes.** Write to a temp file, then `mv` to the target. This prevents the Python process from reading a half-written file.

**Schema prompt augmentation.** When schema is set, the hook appends the same formatting instructions currently in `step.py`:

```
Respond with ONLY a JSON object. The keys and their expected types are:
{schema_json}

Replace the type descriptions with actual values.
Return ONLY the JSON object, no other text.
```

**Session isolation.** The hook reads `session_id` from the state file and compares it to `session_id` from the hook input JSON. If they differ, the hook exits 0 immediately. This prevents the hook from interfering with other Claude Code sessions in the same project.

**PID liveness check.** Before blocking, the hook checks if the Python process is still alive (`kill -0 $python_pid`). If Python crashed, the hook cleans up the state file and exits 0.

**`set +e` guards in Phase 2.** All `jq` calls in Phase 2 (not just the transcript parsing ones) must be wrapped in `set +e` / `set -e` guards. If any `jq` call fails on unexpected state file content while `set -euo pipefail` is active, the hook will die with no cleanup, leaving the state file in an indeterminate state. Python would then wait the full `RESPONSE_TIMEOUT` before raising.

**Integer decisecond arithmetic.** The polling loop uses integer decisecond counters instead of `bc -l` for elapsed time comparison. This eliminates ~1,500 `bc` subprocess invocations over a 5-minute timeout at 200ms polling intervals.

### Full script specification

```bash
#!/bin/bash
set -euo pipefail

HOOK_INPUT=$(cat)
STATE_FILE=".claude/auto-loop.json"
TIMEOUT=300  # seconds -- must be less than Claude Code's hard hook timeout

# Log timeout for debuggability (appears in Claude Code's hook debug output)
echo "[auto] stop-hook invoked (internal timeout: ${TIMEOUT}s)" >&2

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
set +e
STATE=$(cat "$STATE_FILE")
STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
STATE_SESSION=$(echo "$STATE" | jq -r '.session_id // ""' 2>/dev/null)
PYTHON_PID=$(echo "$STATE" | jq -r '.python_pid // 0' 2>/dev/null)
STEP_NUMBER=$(echo "$STATE" | jq -r '.step_number // 0' 2>/dev/null)
set -e

# If jq failed (invalid JSON), STATUS will be empty -- bail cleanly
if [[ -z "$STATUS" ]]; then
  echo "[auto] state file is invalid JSON, bailing" >&2
  exit 0
fi

# --- Session isolation ---
set +e
HOOK_SESSION=$(echo "$HOOK_INPUT" | jq -r '.session_id // ""' 2>/dev/null)
set -e
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
  set +e
  TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | jq -r '.transcript_path' 2>/dev/null)
  set -e

  LAST_OUTPUT=""
  if [[ -f "$TRANSCRIPT_PATH" ]]; then
    LAST_LINES=$(grep '"role":"assistant"' "$TRANSCRIPT_PATH" | tail -n 100)
    if [[ -n "$LAST_LINES" ]]; then
      set +e
      LAST_OUTPUT=$(echo "$LAST_LINES" | jq -rs '
        map(.message.content[]? | select(.type == "text") | .text) | last // ""
      ' 2>/dev/null)
      set -e
    fi
  fi

  # Write response atomically
  TEMP_FILE="${STATE_FILE}.tmp.$$"
  set +e
  echo "$STATE" | jq \
    --arg status "responded" \
    --arg response "$LAST_OUTPUT" \
    --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.status = $status | .response = $response | .updated_at = $updated_at' \
    > "$TEMP_FILE" 2>/dev/null
  JQ_EXIT=$?
  set -e
  if [[ $JQ_EXIT -ne 0 ]]; then
    echo "[auto] jq failed writing responded state, bailing" >&2
    rm -f "$TEMP_FILE"
    exit 0
  fi
  mv "$TEMP_FILE" "$STATE_FILE"
fi

# --- Phase 2: Wait for next instruction from Python ---
# Use integer decisecond arithmetic to avoid bc subprocess overhead.
# At 200ms poll interval over 5 minutes: 1500 iterations, zero bc calls.
TIMEOUT_DS=$(( TIMEOUT * 10 ))  # deciseconds
ELAPSED_DS=0
POLL_INTERVAL_DS=2              # 2 deciseconds = 200ms

while (( ELAPSED_DS < TIMEOUT_DS )); do
  # Re-read state file (all jq wrapped in set +e)
  if [[ ! -f "$STATE_FILE" ]]; then
    exit 0
  fi

  set +e
  STATE=$(cat "$STATE_FILE")
  STATUS=$(echo "$STATE" | jq -r '.status' 2>/dev/null)
  set -e

  if [[ -z "$STATUS" ]]; then
    echo "[auto] state file became invalid JSON in Phase 2, bailing" >&2
    exit 0
  fi

  # Check Python is still alive
  if [[ "$PYTHON_PID" -gt 0 ]] && ! kill -0 "$PYTHON_PID" 2>/dev/null; then
    rm -f "$STATE_FILE"
    exit 0
  fi

  if [[ "$STATUS" == "pending" ]]; then
    set +e
    INSTRUCTION=$(echo "$STATE" | jq -r '.instruction // ""' 2>/dev/null)
    SCHEMA=$(echo "$STATE" | jq -r '.schema // "null"' 2>/dev/null)
    set -e

    # Build prompt with schema instructions if needed
    PROMPT="$INSTRUCTION"
    if [[ "$SCHEMA" != "null" ]] && [[ "$SCHEMA" != "" ]]; then
      set +e
      SCHEMA_DESC=$(echo "$SCHEMA" | jq '.' 2>/dev/null)
      set -e
      PROMPT="${PROMPT}

Respond with ONLY a JSON object. The keys and their expected types are:
${SCHEMA_DESC}

Replace the type descriptions with actual values. For example, if the schema is {\"name\": \"str\", \"age\": \"int\"}, you would return {\"name\": \"Alice\", \"age\": 30}.
Return ONLY the JSON object, no other text."
    fi

    # Mark as running atomically
    TEMP_FILE="${STATE_FILE}.tmp.$$"
    set +e
    echo "$STATE" | jq \
      --arg status "running" \
      --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      '.status = $status | .updated_at = $updated_at' \
      > "$TEMP_FILE" 2>/dev/null
    JQ_EXIT=$?
    set -e
    if [[ $JQ_EXIT -ne 0 ]]; then
      echo "[auto] jq failed writing running state, bailing" >&2
      rm -f "$TEMP_FILE"
      exit 0
    fi
    mv "$TEMP_FILE" "$STATE_FILE"

    # Block and inject instruction
    set +e
    jq -n --arg reason "$PROMPT" '{"decision": "block", "reason": $reason}'
    set -e
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
  ELAPSED_DS=$(( ELAPSED_DS + POLL_INTERVAL_DS ))
done

# Timeout: write error and let Claude stop
TEMP_FILE="${STATE_FILE}.tmp.$$"
set +e
echo "$STATE" | jq \
  --arg status "error" \
  --arg error "stop hook timed out waiting for next instruction (${TIMEOUT}s)" \
  --arg updated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '.status = $status | .error = $error | .updated_at = $updated_at' \
  > "$TEMP_FILE" 2>/dev/null
set -e
if [[ -f "$TEMP_FILE" ]]; then
  mv "$TEMP_FILE" "$STATE_FILE"
fi
exit 0
```

---

## 3. Python Step Implementation

**Path:** `src/auto/step.py` (replaces current content)

### Public API (unchanged)

```python
async def run_program(program_fn):
    """Execute an auto program using stop-hook IPC.

    Writes instructions to .claude/auto-loop.json and polls for responses.
    Must be run as a sidecar while a Claude Code session is active.
    Must be started from the project root (the directory containing .claude/).
    """
```

The `step()` function signature adds a `schema_strict` parameter:

```python
async def step(instruction: str, schema: dict | None = None, schema_strict: bool = True) -> str | dict:
```

### Implementation

```python
"""Auto: the step() primitive via Claude Code stop hook IPC.

The Python program runs as a sidecar process alongside a Claude Code TUI session.
Communication happens through .claude/auto-loop.json. A stop hook installed in
the project's .claude/hooks.json intercepts Claude's turn endings and relays
instructions/responses.

IMPORTANT: auto-run MUST be invoked from the project root (the directory
containing .claude/). The state file path .claude/auto-loop.json is resolved
relative to the git repo root detected at startup. If run from a different
directory, the hook (which runs in the project root) will not find the state
file and the loop will never start.

    async def main(step):
        result = await step("run train.py, report loss")
        await step(f"loss was {result}, try to improve it")

step(instruction) -> str
step(instruction, schema={...}) -> dict
step(instruction, schema={...}, schema_strict=False) -> dict (nulls on parse failure)
"""

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


# --- Constants ---

POLL_INTERVAL = 0.1  # 100ms
RESPONSE_TIMEOUT = 600  # 10 minutes


# --- CWD / state file resolution ---

def _find_repo_root() -> Path:
    """Find the git repo root starting from cwd. Falls back to cwd if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def _state_file_path() -> Path:
    """Resolve the state file path relative to the git repo root."""
    return _find_repo_root() / ".claude" / "auto-loop.json"


# --- JSON extraction (preserved from current step.py) ---

def _extract_json(text):
    """Extract JSON object from model response, handling markdown fences and surrounding text.

    Raises ValueError if no valid JSON can be extracted.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    for start_char, end_char in [('{', '}'), ('[', ']')]:
        end = text.rfind(end_char)
        if end == -1:
            continue
        depth = 0
        for i in range(end, -1, -1):
            if text[i] == end_char:
                depth += 1
            elif text[i] == start_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:end + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract valid JSON from response: {text[:200]}")


# --- State file I/O ---

def _write_state(data: dict) -> None:
    """Write state file atomically."""
    state_path = _state_file_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    fd, temp_path = tempfile.mkstemp(
        dir=state_path.parent,
        prefix=".auto-loop-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(temp_path, state_path)
    except:
        os.unlink(temp_path)
        raise


def _read_state() -> dict | None:
    """Read state file. Returns None if file doesn't exist."""
    try:
        with open(_state_file_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# --- Core loop ---

async def _wait_for_response(step_number: int) -> str:
    """Poll state file until status becomes 'responded' or 'error'.

    Returns the response text.
    Raises RuntimeError on error or timeout.
    """
    deadline = time.monotonic() + RESPONSE_TIMEOUT

    while time.monotonic() < deadline:
        state = _read_state()

        if state is None:
            raise RuntimeError("State file disappeared -- hook or session ended")

        if state.get("status") == "responded" and state.get("step_number") == step_number:
            return state.get("response", "")

        if state.get("status") == "error":
            raise RuntimeError(f"Hook error: {state.get('error', 'unknown')}")

        await asyncio.sleep(POLL_INTERVAL)

    raise RuntimeError(f"Timeout waiting for response (step {step_number}, {RESPONSE_TIMEOUT}s)")


async def run_program(program_fn):
    """Execute an auto program using stop-hook IPC.

    Writes instructions to .claude/auto-loop.json and polls for responses.
    A stop hook installed in the Claude Code session reads these instructions
    and injects them as Claude's next turn.

    MUST be run from the project root (directory containing .claude/).

    Args:
        program_fn: An async function that takes step as its argument.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pid = os.getpid()
    cwd = str(Path.cwd().resolve())

    print(f"[auto] Starting (PID {pid}, cwd={cwd})")
    if session_id:
        print(f"[auto] Session: {session_id}")
    else:
        print("[auto] WARNING: CLAUDE_CODE_SESSION_ID not set, session isolation disabled")

    step_count = 0

    # Write a "starting" heartbeat before calling program_fn so that
    # _start_program's bootstrap wait can detect Python is alive even if
    # program_fn does substantial initialization before its first step() call.
    # The hook treats "starting" like "pending" for Phase 2 (falls through to
    # polling), so no instruction is injected until step() writes "pending".
    _write_state({
        "status": "starting",
        "session_id": session_id,
        "step_number": 0,
        "instruction": None,
        "schema": None,
        "response": None,
        "error": None,
        "python_pid": pid,
        "cwd": cwd,
    })

    async def step(instruction, schema=None, schema_strict=True):
        """Send an instruction to Claude and get the result.

        Args:
            instruction: What to do. Natural language.
            schema: If provided, returns structured JSON output.
            schema_strict: If True (default), raises ValueError on JSON parse
                failure. If False, returns {k: None for k in schema} instead.

        Returns:
            str (default) or dict (if schema provided).

        Raises:
            ValueError: if schema is provided, schema_strict is True, and the
                response cannot be parsed as valid JSON.

        Note: Call step() as early as possible in program_fn, before expensive
            initialization. This ensures the hook sees "pending" quickly and
            the bootstrap turn's instruction is injected without delay.
        """
        nonlocal step_count
        step_count += 1
        print(f"[auto] Step {step_count}: {instruction[:80]}...")

        # Write pending instruction
        _write_state({
            "status": "pending",
            "session_id": session_id,
            "step_number": step_count,
            "instruction": instruction,
            "schema": schema,
            "response": None,
            "error": None,
            "python_pid": pid,
            "cwd": cwd,
        })

        # Wait for response
        response_text = await _wait_for_response(step_count)

        if schema is None:
            result_preview = response_text[:100] if response_text else "(empty)"
            print(f"[auto] Step {step_count} result: {result_preview}")
            return response_text

        # Parse JSON from response
        try:
            result = _extract_json(response_text)
        except ValueError as e:
            if schema_strict:
                raise ValueError(
                    f"[auto] Step {step_count}: JSON parse failed (schema_strict=True). "
                    f"Response was: {response_text[:200]}"
                ) from e
            print(f"[auto] WARNING: Step {step_count}: JSON parse failed, returning nulls for schema keys")
            result = {k: None for k in schema}

        result_preview = json.dumps(result)[:100]
        print(f"[auto] Step {step_count} result: {result_preview}")
        return result

    try:
        await program_fn(step)
        print(f"[auto] Program complete ({step_count} steps)")
    finally:
        # Signal done
        _write_state({
            "status": "done",
            "session_id": session_id,
            "step_number": step_count,
            "instruction": None,
            "schema": None,
            "response": None,
            "error": None,
            "python_pid": pid,
            "cwd": cwd,
        })
```

### Differences from current step.py

1. **No HTTP client.** No httpx, no server URL, no session management API.
2. **`schema_strict` parameter on `step()`.** By default (`schema_strict=True`), JSON parse failure raises `ValueError`. Pass `schema_strict=False` to get the null-fallback behavior. Silent data loss is no longer the default.
3. **`_extract_json` always raises on failure.** The function always raises `ValueError` when no JSON can be found. The caller (`step()`) decides what to do based on `schema_strict`.
4. **CWD-aware state file path.** `_state_file_path()` resolves relative to the git repo root, not the process's current directory. This handles cases where the subprocess's `cwd` differs from the hook's project root.
5. **Session isolation warning.** `run_program` logs a visible warning when `CLAUDE_CODE_SESSION_ID` is not set.
6. **`cwd` field in state.** Written to state file for debugging.
7. **No `server_url`, `cwd`, `session_id` parameters on `run_program`.** The session is implicit (whichever Claude Code session has the hook installed). The CWD is the project root. The session ID comes from `CLAUDE_CODE_SESSION_ID` env var.
8. **Blocking on file polls instead of HTTP response.** Uses `asyncio.sleep` to avoid blocking the event loop.

### Migration Note: `schema_strict=True` is a breaking change

**This is a breaking change from the previous null-fallback behavior.**

In the previous `step.py`, a JSON parse failure when `schema` was provided silently returned `{k: None for k in schema}`. With this implementation, `schema_strict=True` is the default and a JSON parse failure raises `ValueError` instead.

Any existing auto program that:
- calls `step(..., schema={...})`, AND
- does not explicitly handle `ValueError`, AND
- relied on `None` values being returned silently on parse failure

will now crash at the first JSON parse failure instead of continuing with null data.

**To migrate:** Either audit your program to handle `ValueError` from `step()` (recommended), or pass `schema_strict=False` explicitly to restore the old behavior: `await step(instruction, schema=schema, schema_strict=False)`.

---

## 4. CLI Changes

**Path:** `src/auto/cli.py`

### Changes

1. **Remove `--backend` flag.** There is only one backend now.
2. **Remove backend selection logic.** No more `opencode` vs `claude` choice.
3. **Add `auto-run setup` command.** Installs the hook into `.claude/hooks.json` for the current project.
4. **Simplify `_start_program`.** Always imports from `auto.step`. Records and passes CWD. Waits for state file before printing "send go" message.
5. **Update help text.**

### New CLI interface

```
Usage: auto-run <command> [args...]

Commands:
    auto-run <program.py>   Start an auto program in background
    auto-run setup          Install stop hook into .claude/hooks.json
    auto-run status         Show running state and recent logs
    auto-run log            Tail the auto.log file
    auto-run stop           Kill running program

Environment Variables:
    CLAUDE_CODE_SESSION_ID  Set automatically by Claude Code
```

### CWD requirement

`auto-run` MUST be run from the project root (the directory that contains `.claude/`). This is where the hook expects to find `.claude/auto-loop.json`. The state file path in both the hook script and Python is resolved relative to this directory.

`_start_program` records the current working directory and passes it to the subprocess via the `--cwd` argument. The subprocess uses this path to resolve the state file location, ensuring correctness even if Python's working directory differs.

### New `_setup_hook` function

```python
def _setup_hook():
    """Install the auto stop hook into .claude/hooks.json."""
    hooks_dir = Path(".claude")
    hooks_dir.mkdir(exist_ok=True)
    hooks_file = hooks_dir / "hooks.json"

    # Resolve path to stop-hook.sh relative to this package
    hook_script = Path(__file__).parent / "hooks" / "stop-hook.sh"

    if not hook_script.exists():
        print(f"Error: stop-hook.sh not found at {hook_script}", file=sys.stderr)
        sys.exit(1)

    # Make sure it's executable
    hook_script.chmod(hook_script.stat().st_mode | 0o755)

    hook_script_abs = str(hook_script.resolve())

    hook_entry = {
        "type": "command",
        "command": hook_script_abs,
    }

    if hooks_file.exists():
        with open(hooks_file) as f:
            config = json.load(f)
    else:
        config = {}

    hooks = config.setdefault("hooks", {})
    stop_hooks = hooks.setdefault("Stop", [])

    # Check if already installed -- compare resolved absolute path exactly
    for group in stop_hooks:
        for h in group.get("hooks", []):
            if h.get("command", "") == hook_script_abs:
                print("[auto] Hook already installed")
                return

    stop_hooks.append({"hooks": [hook_entry]})

    with open(hooks_file, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[auto] Installed stop hook: {hook_script_abs}")
    print(f"[auto] Config written to: {hooks_file.resolve()}")
    print(f"[auto] NOTE: Restart Claude Code for hook changes to take effect.")
    print(f"[auto] To verify: check Claude Code Settings > Hooks for the installed entry.")
```

**Idempotency note:** The check compares the resolved absolute path of the hook script exactly (not substring matching). This prevents false positives if another hook at a path containing "auto" or "stop-hook" is already installed.

**hooks.json format note:** The nested structure `{"hooks": {"Stop": [{"hooks": [...]}]}}` matches the format used by Ralph Loop, an Anthropic-maintained reference plugin. Each entry in the `Stop` array is a hook group object with a `hooks` array. Each hook in that array has `type` and `command` fields. This structure has been verified against a working Claude Code installation.

**Restart requirement:** Hook changes to `hooks.json` may not take effect until Claude Code is restarted. `auto-run setup` prints a reminder. If the user runs `auto-run setup` while Claude Code is already open, the hook will not activate until the session is restarted.

**Verification:** After setup, the user should verify the hook appears in Claude Code's Settings > Hooks panel. `auto-run setup` can optionally check that the hooks file was written correctly by re-reading and printing the Stop hooks list.

### Updated `_start_program`

```python
def _start_program(program_path):
    """Start an auto program as a background process."""
    if not os.path.isfile(program_path):
        print(f"Error: {program_path} not found", file=sys.stderr)
        sys.exit(1)

    # Verify hook is installed
    hooks_file = Path(".claude/hooks.json")
    if not hooks_file.exists():
        print("Error: Stop hook not installed. Run 'auto-run setup' first.", file=sys.stderr)
        sys.exit(1)

    # Check if already running
    if os.path.isfile(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Error: Auto program already running (PID {old_pid})", file=sys.stderr)
            print("Use 'auto-run stop' first", file=sys.stderr)
            sys.exit(1)
        except ProcessLookupError:
            os.remove(PID_FILE)

    program_path = os.path.abspath(program_path)
    cwd = os.getcwd()  # project root -- must match hook's working directory

    proc = subprocess.Popen(
        [sys.executable, "-c", f"""
import asyncio, importlib.util, sys, os
# Set cwd to project root so state file resolves correctly
os.chdir({cwd!r})
sys.path.insert(0, os.getcwd())
spec = importlib.util.spec_from_file_location('program', {program_path!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from auto.step import run_program
asyncio.run(run_program(mod.main))
"""],
        stdout=open(LOG_FILE, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    print(f"[auto] Started in background (PID {proc.pid})")
    print(f"[auto] Logs: {LOG_FILE}")

    # Wait for Python to write any state (starting or pending) before printing
    # the "send go" message. run_program writes a "starting" heartbeat before
    # calling program_fn, so the file appears as soon as Python is alive even
    # if program_fn does slow initialization before its first step() call.
    state_file = Path(".claude/auto-loop.json")
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if state_file.exists():
            break
        time.sleep(0.05)
    else:
        print(f"[auto] WARNING: state file not written within 3s, Python may still be starting")

    print(f"[auto] Monitor: auto-run status")
    print(f"[auto] Send any message to Claude to begin (e.g., 'go')")
```

---

## 5. First Step Bootstrap

This is the trickiest part. The stop hook only fires when Claude **finishes** a turn. But for the very first step, Claude hasn't started yet -- there's no turn to finish.

### Solution: Bootstrap via initial user message

The `_start_program` function (or the user/model invoking `auto-run`) must send an initial message to Claude to trigger the first turn. When Claude finishes that bootstrap turn, the stop hook fires, finds the `pending` instruction from Python, and injects it.

**The bootstrap message** should be minimal. Something like:

> "Starting auto program. The stop hook will inject the first instruction."

Or even simpler: the user can just send any message. The auto program writes its first `pending` instruction immediately on startup. When Claude finishes its response to whatever message was sent, the hook fires, sees `pending`, and blocks with the real instruction.

### Recommended approach: `auto-run` tells the user what to do

When `auto-run program.py` starts:

```
[auto] Started in background (PID 12345)
[auto] Logs: auto.log
[auto] Monitor: auto-run status
[auto] Send any message to Claude to begin (e.g., "go")
```

This is the simplest approach with zero magic. The user sends "go" (or any text), Claude responds (possibly something like "What would you like me to do?"), the stop hook fires, sees the pending instruction from the Python program, and injects it. Claude then processes the real instruction.

### Bootstrap race window

`_start_program` waits up to 3 seconds for the state file to appear before printing the "send go" message. This prevents the user from triggering a Claude turn before Python has written its first `pending` instruction.

The race window that remains: between Python writing the state file and the user sending "go", Python must have written `pending`. With the 3-second wait, this window is effectively eliminated on any machine where Python startup is faster than 3 seconds (which includes all normal cases -- Python startup with asyncio is typically under 500ms).

### Chosen approach: require the user to send "go"

When the model itself runs `auto-run program.py`, it can immediately send "go" as its next action. This is natural in the TUI flow. The SKILL.md will document this pattern.

---

## 6. Session Isolation

Identical to Ralph Loop's approach:

1. Python writes `CLAUDE_CODE_SESSION_ID` into the state file's `session_id` field.
2. The hook reads `session_id` from both the state file and the hook input JSON.
3. If they don't match, the hook exits 0 immediately.
4. If the state file has no `session_id` (legacy), the hook proceeds (permissive).

`CLAUDE_CODE_SESSION_ID` is an environment variable set by Claude Code for each session. It's available to both the Python sidecar (if launched from within the session) and the hook (via hook input JSON's `session_id` field).

### Edge case: Python launched from a different terminal

If the user runs `auto-run program.py` from a regular terminal (not from within Claude Code), `CLAUDE_CODE_SESSION_ID` won't be set. In this case:

- Python writes `session_id: ""` (empty string).
- Python logs: `[auto] WARNING: CLAUDE_CODE_SESSION_ID not set, session isolation disabled`
- The hook sees empty `STATE_SESSION` and skips the isolation check (same as Ralph Loop's legacy behavior).
- This means the hook will fire in ANY Claude Code session for this project.
- This is acceptable for single-session use but could cause issues with multiple sessions.

To handle this properly, `auto-run setup` could accept a `--session` flag, or the user could set `CLAUDE_CODE_SESSION_ID` manually. But for v1, the empty-string fallback is fine.

---

## 7. Error Handling

### Python process crashes

1. The hook checks `kill -0 $python_pid` on every invocation.
2. If Python is dead, the hook removes the state file and exits 0.
3. Claude stops normally.
4. The stale `.auto.pid` file can be cleaned up by `auto-run status` or `auto-run stop`.

### Claude Code crashes or user quits

1. Python is polling `_wait_for_response()` with a timeout.
2. If no response comes within `RESPONSE_TIMEOUT` (10 minutes), Python raises `RuntimeError`.
3. The `run_program` `finally` block writes `done` to the state file (cleanup).
4. The program exits, and the user sees the error in `auto.log`.

### Hook timeout

1. The hook has a 5-minute internal timeout (`TIMEOUT=300`). This value must be less than Claude Code's hard hook execution timeout. The internal timeout is logged at startup.
2. If Python doesn't produce the next instruction in time, the hook writes `error` status and exits 0.
3. Python sees the error status, raises `RuntimeError`, and exits.
4. `auto-run setup` should verify the configured timeout by reading the installed hook script and confirming `TIMEOUT` is set to a safe value.

### State file corruption

1. If the state file contains invalid JSON, both the hook and Python treat it as "no active loop" and stop gracefully.
2. The hook uses `jq` for parsing inside `set +e` guards. If `jq` fails, STATUS is empty. The hook checks for empty STATUS and exits 0 cleanly.
3. Python uses `json.load` in a try/except.

### Transcript file missing

1. If the transcript JSONL file doesn't exist or can't be parsed, the hook writes an empty response.
2. Python receives an empty string, which may cause JSON parsing to fail if a schema was expected.
3. If `schema_strict=True` (default), `step()` raises `ValueError`. If `schema_strict=False`, returns `{k: None for k in schema}`.

### Race conditions

The main race is between Python writing `pending` and the hook reading the state file. Atomic writes (`mv` from temp file) prevent torn reads. The `step_number` field prevents the hook from re-reading a stale `pending` status from a previous step.

---

## 8. Schema / JSON Parsing

### How it works

1. Python `step("do X", schema={"score": "float"})` writes `schema: {"score": "float"}` into the state file.
2. The hook reads the schema and appends JSON formatting instructions to the prompt before injecting it into Claude.
3. Claude responds with (hopefully) a JSON object.
4. The hook extracts the raw assistant text from the transcript and writes it as the `response` field. The hook does NOT parse JSON -- it passes raw text.
5. Python reads the response text and calls `_extract_json()` to parse it.
6. If parsing fails and `schema_strict=True` (default), `step()` raises `ValueError`.
7. If parsing fails and `schema_strict=False`, Python returns `{k: None for k in schema}` with a visible warning log.

### Why the hook doesn't parse JSON

- Keeps the hook simple (bash + jq, no complex logic).
- Python already has the `_extract_json` function with multiple fallback strategies.
- If JSON extraction fails, Python can decide what to do (retry, fallback, raise).

### `_extract_json` contract

`_extract_json(text)` always raises `ValueError` when no valid JSON can be extracted. It never returns a sentinel value. The caller (`step()`) is responsible for deciding what to do with the failure based on `schema_strict`.

### JSON retry (future enhancement)

In the current OpenCode backend, if JSON extraction fails, the system sends a follow-up message asking the model to reformat. This requires an additional step injection, which would work like this:

1. Python detects JSON parse failure.
2. Python writes a new `pending` instruction: "Your previous response was not valid JSON. Return ONLY a JSON object with these keys: {schema}."
3. The hook picks it up on the next stop event (but wait -- Claude already stopped because the hook exited 0 after writing `responded`).

This is the fundamental problem. Once the hook exits 0 and Claude stops, there's no way to inject a follow-up instruction without the user sending another message. For v1, we skip the retry mechanism and accept the first extraction attempt. The schema prompt instructions are usually sufficient for Claude to produce valid JSON.

---

## 9. Setup and Teardown

### Setup flow

1. User (or model) runs `auto-run setup` in the project directory.
2. This creates/updates `.claude/hooks.json` to include the auto stop hook.
3. The hook script path is resolved to an absolute path pointing into the installed package.
4. `auto-run setup` verifies the hooks file was written correctly by re-reading it.
5. `auto-run setup` prints a reminder that Claude Code must be restarted for the change to take effect.
6. `auto-run setup` checks for `jq` and `git` availability and warns if either is missing. (`jq` is required by the hook script; `git` is required by `_find_repo_root()` in `step.py`.)
7. After restarting Claude Code, the user should verify the hook appears in Claude Code's Settings > Hooks panel.

### Starting a loop

1. User (or model) runs `auto-run program.py` **from the project root**.
2. Python process starts in background, writes `starting` heartbeat to `.claude/auto-loop.json` immediately (before `program_fn` runs).
3. `_start_program` waits up to 3 seconds for the state file to appear (any status) before printing the "send go" message.
4. `program_fn` runs; on its first `step()` call, Python writes `pending`.
5. User sends a message to Claude (e.g., "go") to trigger the first turn.
6. Hook fires on Claude's response, picks up the `pending` instruction, injects it.
7. Loop continues until the program writes `done`.

### Teardown

**Normal completion:**
1. Python program finishes `main()`.
2. `run_program`'s `finally` block writes `done` to state file.
3. Next time the hook fires, it sees `done`, removes the state file, exits 0.
4. Claude stops normally.

**Manual stop:**
1. User runs `auto-run stop`.
2. Python process receives SIGTERM, exits.
3. `finally` block writes `done` (if it gets to run; SIGTERM is catchable in Python).
4. If Python dies hard, the hook detects the dead PID on its next invocation and cleans up.

**Emergency stop:**
1. User runs `/cancel-auto` (new slash command, see below) or manually deletes `.claude/auto-loop.json`.
2. Hook sees no state file, exits 0.
3. Claude stops.
4. Python is still running but will timeout waiting for a response and exit.

---

## 10. Repository Structure Changes

```
src/auto/
    __init__.py          [MODIFY] Update imports
    cli.py               [MODIFY] Remove --backend, add 'setup' command
    step.py              [REPLACE] New file-based IPC implementation
    step_claude.py       [DELETE] No longer needed
    state.py             [NO CHANGE] User program state (auto-state.json)
    hooks/
        stop-hook.sh     [NEW] The stop hook script
        hooks.json       [NEW] Hook configuration template

.gitignore               [MODIFY] Add .claude/auto-loop.json explicitly

tests/
    test_step.py         [MODIFY] Update tests for new step.py
    test_integration.py  [MODIFY] Update for hook-based integration
    test_state.py        [NO CHANGE]

docs/
    quickstart.md        [MODIFY] Update setup instructions
    api.md               [MODIFY] Remove server_url references
    design.md            [NO CHANGE] Keep as historical reference

skills/auto/
    SKILL.md             [MODIFY] Update instructions for hook-based flow

pyproject.toml           [MODIFY] Remove httpx dependency

README.md                [MODIFY] Update architecture description
```

### New files

**`src/auto/hooks/stop-hook.sh`**
- The bash stop hook script as specified in Section 2.
- Must be executable (`chmod +x`).
- Shipped as part of the Python package.

**`src/auto/hooks/hooks.json`**
- Template for the hooks configuration. Used by `auto-run setup` as reference, but the actual installation writes to `.claude/hooks.json` in the project directory.
- The nested structure matches the format used by Ralph Loop (an Anthropic-maintained reference plugin). Verified against a working Claude Code installation.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/absolute/path/to/stop-hook.sh"
          }
        ]
      }
    ]
  }
}
```

### Modified files

**`src/auto/__init__.py`**
```python
from auto.step import run_program, _extract_json
from auto import state
```
(Same as before, but `step_claude` import is gone.)

**`pyproject.toml`**
```toml
dependencies = []
```
(Remove `httpx`. No external dependencies needed.)

---

## 11. Detailed Implementation Plan

### Phase 1: Core IPC (day 1)

1. Write `src/auto/hooks/stop-hook.sh` with full logic.
2. Rewrite `src/auto/step.py` with file-based IPC.
3. Manual integration test: start Python program, manually create/edit state file, verify round-trip.

### Phase 2: CLI + Hook Installation (day 1)

4. Update `src/auto/cli.py`: remove `--backend`, add `setup` command.
5. Verify `auto-run setup` correctly writes to `.claude/hooks.json`.
6. Verify `auto-run program.py` starts the sidecar correctly.

### Phase 3: End-to-End Test (day 2)

7. Open Claude Code TUI in the auto project directory.
8. Run `auto-run setup` to install the hook.
9. Run `auto-run examples/autoresearch.py` (or a simpler test program).
10. Send "go" in Claude Code.
11. Verify the loop runs, instructions flow, responses come back.

### Phase 4: Cleanup (day 2)

12. Delete `src/auto/step_claude.py`.
13. Update `__init__.py`.
14. Update tests.
15. Update `pyproject.toml` (remove httpx).
16. Update documentation.

### Phase 5: Skill Update (day 2)

17. Update `skills/auto/SKILL.md` to document the hook-based flow.
18. Remove references to `opencode serve` and `--backend`.
19. Document the "send go to begin" pattern.

---

## 12. Risk Assessment

### Risk: Hook timeout causes lost work

**Severity:** Medium
**Probability:** Low (5-minute timeout is generous)
**Mitigation:** The hook writes `error` status. Python logs the error. User program state in `auto-state.json` is preserved. User can restart.

### Risk: Race condition between Python write and hook read

**Severity:** Low
**Probability:** Low (atomic writes via mv)
**Mitigation:** Atomic file writes on both sides. `step_number` validation prevents stale reads.

### Risk: jq not installed on user's system

**Severity:** High (hook won't work at all)
**Probability:** Low (jq is common; installed by default on many systems)
**Mitigation:** `auto-run setup` checks for `jq` and `git` and warns if either is missing. The hook script should check for `jq` at the top and fail with a clear error message.

### Risk: Claude produces no text in a turn (only tool calls)

**Severity:** Low
**Probability:** Medium (common for action-heavy steps)
**Mitigation:** The hook extracts the last text block. If there is no text block, the response is empty string. This is fine for steps without schema (the step's value is its side effects). For schema steps with `schema_strict=True`, Python raises `ValueError`. A future enhancement could look at the `result` message type as a fallback.

### Risk: Multiple auto programs in same project

**Severity:** Medium (state file collision)
**Probability:** Low (unusual usage)
**Mitigation:** The PID check in `auto-run` prevents starting a second program. The single state file enforces one loop per project.

### Risk: Large transcript JSONL causes slow hook execution

**Severity:** Low
**Probability:** Medium (long sessions)
**Mitigation:** The hook only reads the last 100 assistant lines (same as Ralph Loop). `tail -n 100` is bounded.

### Risk: The "send go to begin" UX is confusing

**Severity:** Medium (poor first experience)
**Probability:** Medium
**Mitigation:** Clear messaging from `auto-run`. The SKILL.md teaches the model to send "go" automatically after running `auto-run`. Future enhancement: auto-inject the first instruction via a bootstrap mechanism that doesn't require user action.

### Risk: auto-run run from wrong directory

**Severity:** High (state file not found by hook)
**Probability:** Medium (easy to do by mistake)
**Mitigation:** Documented prominently. `_start_program` records and passes cwd. The state file path resolution in `step.py` uses git repo root detection as a fallback. Error messages include the resolved state file path for debugging.

### Risk: Claude Code kills the hook before internal timeout

**Severity:** Medium (state file stuck at running/responded)
**Probability:** Unknown (depends on Claude Code's hard timeout)
**Mitigation:** The hook logs its internal timeout at startup. `auto-run setup` documents that the internal timeout must be less than Claude Code's configured hook timeout. Python's `RESPONSE_TIMEOUT` (10 minutes) provides the final safety net.

---

## 13. Known Limitations

### Concurrent hook invocation

If Claude Code fires the stop hook twice in rapid succession (e.g., during a multi-tool turn where multiple stop events are emitted), two hook processes may enter Phase 2 simultaneously. Both would see Python write `pending`, and both would output block decisions with the next instruction. Claude Code would receive two block decisions and process both.

**Impact:** The instruction for step N+1 may be injected twice, causing Claude to process it as two separate turns. Python would receive two `responded` entries but only read the first one (because `_wait_for_response` returns on the first `responded` it sees with the correct `step_number`). The second `responded` would be overwritten when Python writes `pending` for step N+2.

**Workaround (Phase 2 implementation):** Add a file lock at hook entry. When a second hook process arrives while the first is still running (including its Phase 2 poll loop), the second process blocks on the lock for up to 30 seconds. Once the first exits and releases the lock, the second re-evaluates the state file from the beginning and handles whatever state Python has reached.

```bash
# Serialize concurrent hook invocations at entry
LOCK_FILE="${STATE_FILE}.lock"
exec 9>"$LOCK_FILE"
flock -w 30 9 || { echo "[auto] lock timeout, another hook held lock too long" >&2; exit 0; }
# Lock is held for the lifetime of this shell process (released on exit)
# ... proceed with Phase 1 / Phase 2 ...
```

Using `flock -w 30` (blocking with a 30-second timeout) instead of `flock -n` (non-blocking) means the second hook waits for the first to finish rather than immediately exiting. After acquiring the lock, the second hook reads the current state and handles whichever phase is appropriate — it does not drop the turn. The `flock -n` (non-blocking) approach must not be used here because it causes the second hook to exit 0 with no work done, leaving Python waiting for a `responded` state that will never arrive, causing a timeout.

This is a known limitation in the current design. It is low probability but should be addressed in a follow-up implementation pass.

---

## 14. Testing Plan

### Unit tests for `_extract_json`

File: `tests/test_step.py`

Test cases:

| Input | Expected output |
|---|---|
| `'{"key": "value"}'` | `{"key": "value"}` |
| `'` ` `json\n{"key": "value"}\n` ` `'` | `{"key": "value"}` |
| `'Here is the result:\n{"key": "value"}\nDone.'` | `{"key": "value"}` |
| `'No JSON here'` | raises `ValueError` |
| `'{"outer": {"inner": 1}}'` | `{"outer": {"inner": 1}}` |
| `''` | raises `ValueError` |
| `'{"key": "value"} extra text'` | `{"key": "value"}` |
| `'[1, 2, 3]'` | `[1, 2, 3]` |
| `'text {"a":1} more text {"b":2}'` | `{"b": 2}` (last object, per `rfind` behavior) |

All `ValueError` cases must raise, not return `None` or a sentinel.

### Unit tests for `_write_state` / `_read_state`

File: `tests/test_step.py`

1. **Atomic write test:** Write state, read it back, verify fields match. Confirm no temp file remains.
2. **Concurrent read test:** Start two threads; one writes in a loop, one reads in a loop. Verify no `JSONDecodeError` is ever raised during concurrent access (atomic write guarantee).
3. **Missing directory test:** Call `_write_state` when `.claude/` does not exist. Verify the directory is created and the file is written successfully.
4. **CWD test:** Set cwd to a directory that is not the project root. Verify `_state_file_path()` resolves to the git repo root's `.claude/auto-loop.json`, not `cwd/.claude/auto-loop.json`.

### Unit tests for `_wait_for_response`

File: `tests/test_step.py`

1. **Normal flow:** Write `responded` state after 100ms delay. Verify `_wait_for_response` returns the response text.
2. **Timeout:** Do not write any state change. Set `RESPONSE_TIMEOUT = 0.5`. Verify `RuntimeError` is raised with "Timeout" in the message.
3. **Error status:** Write `error` state. Verify `RuntimeError` is raised with the error message.
4. **File disappears:** Delete the state file mid-poll. Verify `RuntimeError` is raised with "disappeared" in the message.
5. **Step number mismatch:** Write `responded` with wrong `step_number`. Verify polling continues until timeout (does not return early on wrong step).

### Hook script tests

File: `tests/test_hook.sh` (bash test harness)

Create a test harness that:

1. Creates a temp directory with `.claude/` subdirectory.
2. Creates mock state files and transcript JSONL in the temp directory.
3. Pipes mock hook input JSON to the hook script via stdin.
4. Captures hook stdout and checks the exit code.
5. Reads the state file after the hook exits to verify state transitions.

Test cases:

| Scenario | Setup | Expected behavior |
|---|---|---|
| **No state file** | No `.claude/auto-loop.json` | Hook exits 0, no output |
| **Session mismatch** | State has `session_id: "A"`, hook input has `session_id: "B"` | Hook exits 0, no output |
| **Dead PID** | State has `python_pid: 99999` (nonexistent), status `pending` | Hook exits 0, state file deleted |
| **pending → running** | Status `pending`, valid instruction, Python PID alive | Hook outputs `{"decision":"block","reason":"..."}`, state becomes `running` |
| **schema prompt augmentation** | Status `pending`, instruction `"score it"`, schema `{"score": "float"}` | Hook outputs block whose `reason` includes "Respond with ONLY a JSON object" and the schema JSON |
| **running → responded** | Status `running`, valid transcript JSONL | State becomes `responded` with response text; hook then enters Phase 2 |
| **responded (Phase 2)** | Status `responded`, then state changes to `pending` after 200ms | Hook outputs block with instruction |
| **done cleanup** | Status `done` | Hook exits 0, state file deleted |
| **hook timeout** | Status `responded`, never changes to `pending` | After timeout, state becomes `error` with timeout message |
| **Invalid JSON state** | State file contains `{invalid` | Hook exits 0 cleanly, no error |

For the "running → responded" and "Phase 2" tests: use a background process to write the state change at the appropriate time while the hook is running.

### Integration test

File: `tests/test_integration.py`

A test script that:

1. Starts a Python program in the background using `run_program`.
2. Simulates the hook by directly reading and writing the state file (bypassing the actual bash script).
3. Verifies multi-step round trips:
   - Write `pending`, hook-simulator reads it and writes `running`, then `responded`.
   - Python reads `responded`, writes next `pending`.
   - Repeat for 3+ steps.
4. Verifies schema/JSON extraction:
   - Step with `schema={"value": "float"}`.
   - Simulate hook writing a JSON response.
   - Verify `step()` returns a dict with the correct key.
5. Verifies `schema_strict=True` raises on bad JSON:
   - Step with `schema={"value": "float"}`, simulate hook writing non-JSON response.
   - Verify `ValueError` is raised.
6. Verifies `schema_strict=False` returns nulls:
   - Same setup, call `step(..., schema_strict=False)`.
   - Verify `{"value": None}` is returned.
7. Verifies `done` cleanup:
   - After `program_fn` returns, verify state file contains `status: done`.

### End-to-end test (manual procedure)

Prerequisite: Claude Code installed and running in the `auto` project directory.

**Step 1: Install the hook**
```
$ auto-run setup
[auto] Installed stop hook: /path/to/stop-hook.sh
[auto] Config written to: /path/to/.claude/hooks.json
[auto] NOTE: Restart Claude Code for hook changes to take effect.
```
Restart Claude Code. Verify the hook appears in Settings > Hooks.

**Step 2: Create a test program**
```python
# /tmp/test_auto.py
async def main(step):
    r1 = await step("Say exactly: STEP_ONE_DONE")
    print(f"Got: {r1}")
    r2 = await step("Say exactly: STEP_TWO_DONE")
    print(f"Got: {r2}")
```

**Step 3: Start the program**
```
$ auto-run /tmp/test_auto.py
[auto] Started in background (PID 12345)
[auto] Logs: auto.log
[auto] Monitor: auto-run status
[auto] Send any message to Claude to begin (e.g., "go")
```

**Step 4: Send "go" in Claude Code TUI**

Expected behavior (hook-invocation-by-invocation trace):

- **Hook 1 fires** (bootstrap "go" turn complete). Phase 1: status is `pending` (or `starting` transitioning to `pending`), not `running` — skip Phase 1. Phase 2: polls, reads `pending` with "Say exactly: STEP_ONE_DONE". Marks state `running`. Outputs `{"decision": "block", "reason": "Say exactly: STEP_ONE_DONE"}`. Exits 0.
- Claude processes the injected step 1 instruction and responds with "STEP_ONE_DONE".
- **Hook 2 fires** (step 1 complete). Phase 1: sees `running`, extracts "STEP_ONE_DONE" from transcript, writes `responded`. Phase 2: polls; Python reads `responded`, processes it, writes `pending` for step 2. Hook reads `pending` with "Say exactly: STEP_TWO_DONE". Marks state `running`. Outputs block with step 2 instruction. Exits 0.
- Claude processes the injected step 2 instruction and responds with "STEP_TWO_DONE".
- **Hook 3 fires** (step 2 complete). Phase 1: sees `running`, extracts "STEP_TWO_DONE" from transcript, writes `responded`. Phase 2: polls; Python reads `responded`, `program_fn` returns, `run_program` finally block writes `done`. Hook reads `done`. Deletes state file. Exits 0. Claude stops.

**Step 5: Verify**
```
$ cat auto.log
[auto] Starting (PID 12345, cwd=/path/to/project)
[auto] Session: <session_uuid>
[auto] Step 1: Say exactly: STEP_ONE_DONE...
[auto] Step 1 result: STEP_ONE_DONE
[auto] Step 2: Say exactly: STEP_TWO_DONE...
[auto] Step 2 result: STEP_TWO_DONE
[auto] Program complete (2 steps)
```

**Step 6: Verify state file is cleaned up**
```
$ ls .claude/auto-loop.json
ls: .claude/auto-loop.json: No such file or directory
```

**How to verify the loop ran correctly:** The auto.log file should show both steps completing with the expected response text. The state file should be gone. Claude Code's TUI should show the two injected turns in the conversation history.

**Step 7: Crash recovery test**

Run the test program again (`auto-run /tmp/test_auto.py`) and send "go". Once Python is in the middle of waiting for step 1's response (state is `running`), kill the Python process hard:
```
$ kill -9 <PID from auto.log>
```

Expected behavior:
- The hook is in Phase 2 polling (or will be on its next invocation).
- Within one poll interval (200ms), `kill -0 $PYTHON_PID` fails.
- Hook deletes `.claude/auto-loop.json` and exits 0.
- Claude stops (no more block decisions from the hook).

Verify:
```
$ ls .claude/auto-loop.json
ls: .claude/auto-loop.json: No such file or directory
```
