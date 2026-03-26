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
import signal
import subprocess
import tempfile
import time
from pathlib import Path


# --- Constants ---

POLL_INTERVAL = 0.1  # 100ms


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
        print("[auto] WARNING: git not available, falling back to cwd for state file path", flush=True)
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

    # Clean orphaned temp files from prior crashes
    for f in state_path.parent.glob(".auto-loop-*.tmp"):
        try:
            f.unlink()
        except OSError:
            pass

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
    except Exception:
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
    Raises RuntimeError on error or if state file disappears.
    Polls indefinitely -- the only exit conditions are:
      - status becomes 'responded' with matching step_number
      - status becomes 'error'
      - state file disappears
    """
    while True:
        state = _read_state()

        if state is None:
            raise RuntimeError("State file disappeared -- hook or session ended")

        if state.get("status") == "responded" and state.get("step_number") == step_number:
            return state.get("response", "")

        if state.get("status") == "error":
            error_step = state.get("step_number", 0)
            if error_step >= step_number:
                raise RuntimeError(f"Hook error: {state.get('error', 'unknown')}")

        await asyncio.sleep(POLL_INTERVAL)


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

    print(f"[auto] Starting (PID {pid}, cwd={cwd})", flush=True)
    if session_id:
        print(f"[auto] Session: {session_id}", flush=True)
    else:
        print("[auto] WARNING: CLAUDE_CODE_SESSION_ID not set, session isolation disabled", flush=True)

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
        instr_preview = instruction[:80] if instruction else "(none)"
        print(f"[auto] Step {step_count}: {instr_preview}...", flush=True)

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
            "transcript_lines": None,
        })

        # Wait for response
        response_text = await _wait_for_response(step_count)

        if schema is None:
            result_preview = response_text[:100] if response_text else "(empty)"
            print(f"[auto] Step {step_count} result: {result_preview}", flush=True)
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
            print(f"[auto] WARNING: Step {step_count}: JSON parse failed, returning nulls for schema keys", flush=True)
            result = {k: None for k in schema}

        result_preview = json.dumps(result)[:100]
        print(f"[auto] Step {step_count} result: {result_preview}", flush=True)
        return result

    def _handle_sigterm(signum, frame):
        raise SystemExit("Received SIGTERM")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        await program_fn(step)
        print(f"[auto] Program complete ({step_count} steps)", flush=True)
    except Exception as e:
        print(f"[auto] Program CRASHED: {e}", flush=True)
        _write_state({
            "status": "error",
            "session_id": session_id,
            "step_number": step_count,
            "instruction": None,
            "schema": None,
            "response": None,
            "error": str(e),
            "python_pid": pid,
            "cwd": cwd,
        })
        raise
    else:
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
