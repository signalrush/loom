"""Auto: the step() primitive via Claude Code stop hook IPC.

The Python program runs as a sidecar process alongside a Claude Code TUI session.
Communication happens through ~/.auto/latest/self.json. A stop hook installed in
the project's settings intercepts Claude's turn endings and relays
instructions/responses.

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
    """Resolve the state file path to ~/.auto/latest/self.json (matches stop-hook.sh)."""
    auto_dir = Path.home() / ".auto" / "latest"
    auto_dir.mkdir(parents=True, exist_ok=True)
    return auto_dir / "self.json"


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

def _log(msg: str) -> None:
    """Print a timestamped log message."""
    ts = time.strftime("%H:%M:%S", time.localtime())
    print(f"[auto] {ts} {msg}", flush=True)


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

    status = data.get("status", "?")
    step_num = data.get("step_number", "?")
    _log(f"_write_state: status={status}, step={step_num}, path={state_path}")

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
    except Exception as e:
        _log(f"_write_state FAILED: {e}")
        os.unlink(temp_path)
        raise

    # Verify the write succeeded by reading back
    verify = _read_state_raw(state_path)
    if verify is None:
        _log(f"_write_state VERIFY FAILED: read-back returned None")
    elif verify.get("status") != status or verify.get("step_number") != step_num:
        _log(f"_write_state VERIFY MISMATCH: wrote status={status}/step={step_num}, "
             f"read status={verify.get('status')}/step={verify.get('step_number')}")


def _read_state_raw(path: Path) -> dict | None:
    """Read state file from explicit path. Returns None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _read_state() -> dict | None:
    """Read state file. Returns None if file doesn't exist."""
    state_path = _state_file_path()
    try:
        with open(state_path) as f:
            data = json.load(f)
        # Check if the file's inode/mtime changed unexpectedly (e.g., git overwrote it)
        return data
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        _log(f"_read_state: JSON decode error at {state_path}: {e}")
        return None
    except OSError as e:
        _log(f"_read_state: OS error at {state_path}: {e}")
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
    poll_count = 0
    last_logged_status = None
    start_time = time.monotonic()
    state_path = _state_file_path()
    _log(f"_wait({step_number}): polling {state_path}")
    last_mtime = None
    while True:
        state = _read_state()

        if state is None:
            elapsed = time.monotonic() - start_time
            _log(f"_wait({step_number}): state file GONE after {elapsed:.1f}s, {poll_count} polls")
            raise RuntimeError("State file disappeared -- hook or session ended")

        cur_status = state.get("status")
        cur_step = state.get("step_number")
        cur_pid = state.get("python_pid")
        cur_tl = state.get("transcript_lines")
        cur_updated = state.get("updated_at", "?")

        # Detect if the file was modified externally (e.g., git checkout)
        try:
            cur_mtime = os.path.getmtime(state_path)
        except OSError:
            cur_mtime = None

        # Log status changes
        status_key = (cur_status, cur_step, cur_updated)
        if status_key != last_logged_status:
            elapsed = time.monotonic() - start_time
            resp_preview = ""
            if cur_status == "responded":
                resp_text = state.get("response", "")
                resp_preview = f", response={len(resp_text or '')}b"
                if resp_text:
                    resp_preview += f" [{resp_text[:60]}...]"
            _log(f"_wait({step_number}): status={cur_status}, file_step={cur_step}, "
                 f"pid={cur_pid}, tl={cur_tl}, updated={cur_updated}{resp_preview} "
                 f"[{elapsed:.1f}s, {poll_count} polls]")
            last_logged_status = status_key
        elif poll_count > 0 and poll_count % 100 == 0:  # every 10s
            elapsed = time.monotonic() - start_time
            mtime_info = ""
            if cur_mtime and last_mtime and cur_mtime != last_mtime:
                mtime_info = " (mtime CHANGED!)"
            _log(f"_wait({step_number}): HEARTBEAT {elapsed:.0f}s, {poll_count} polls, "
                 f"status={cur_status}, file_step={cur_step}, pid={cur_pid}, "
                 f"tl={cur_tl}, updated={cur_updated}{mtime_info}")

        last_mtime = cur_mtime

        if cur_status == "responded" and cur_step == step_number:
            resp = state.get("response", "")
            elapsed = time.monotonic() - start_time
            _log(f"_wait({step_number}): MATCHED after {elapsed:.1f}s, response={len(resp or '')}b")
            return resp

        # Detect step_number mismatch (possible git overwrite)
        if cur_status == "responded" and cur_step != step_number:
            _log(f"_wait({step_number}): WARNING step mismatch! file has step={cur_step}, "
                 f"we want step={step_number}. Possible state file corruption (git?).")

        if cur_status == "error":
            error_step = state.get("step_number", 0)
            if error_step >= step_number:
                raise RuntimeError(f"Hook error: {state.get('error', 'unknown')}")

        poll_count += 1
        await asyncio.sleep(POLL_INTERVAL)


async def run_program(program_fn):
    """Execute an auto program using stop-hook IPC.

    Writes instructions to ~/.auto/latest/self.json and polls for responses.
    A stop hook installed in the Claude Code session reads these instructions
    and injects them as Claude's next turn.

    Args:
        program_fn: An async function that takes step as its argument.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pid = os.getpid()
    cwd = str(Path.cwd().resolve())

    import datetime
    print(f"\n{'='*60}", flush=True)
    _log(f"{datetime.datetime.now().isoformat()} Starting (PID {pid}, cwd={cwd})")
    _log(f"State file: {_state_file_path()}")
    if session_id:
        _log(f"Session: {session_id}")
    else:
        _log("WARNING: CLAUDE_CODE_SESSION_ID not set, session isolation disabled")

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
        schema_keys = list(schema.keys()) if schema else None
        _log(f"Step {step_count}: {instr_preview}...")
        _log(f"Step {step_count}: schema={schema_keys}, strict={schema_strict}")

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
        _log(f"Step {step_count}: waiting for hook to deliver response...")
        response_text = await _wait_for_response(step_count)
        resp_len = len(response_text) if response_text else 0
        resp_preview = (response_text[:120] + "...") if response_text and len(response_text) > 120 else response_text
        _log(f"Step {step_count}: got response ({resp_len}b): {resp_preview!r}")

        if schema is None:
            result_preview = response_text[:100] if response_text else "(empty)"
            _log(f"Step {step_count} result (text): {result_preview}")
            return response_text

        # Parse JSON from response, retry up to 3 times
        for attempt in range(3):
            try:
                result = _extract_json(response_text)
                _log(f"Step {step_count}: JSON parse OK (attempt {attempt + 1}/3), keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")
                break
            except ValueError as parse_err:
                _log(f"Step {step_count}: JSON parse FAILED (attempt {attempt + 1}/3): {parse_err}")
                _log(f"Step {step_count}: response was ({resp_len}b): {(response_text or '')[:200]!r}")
                if attempt < 2:
                    # Retry: ask the model to reformat
                    step_count += 1
                    retry_prompt = f"Your previous response was not valid JSON. Respond with a JSON object with these keys: {json.dumps(schema)}"
                    _log(f"Step {step_count}: sending JSON retry {attempt + 1}/2...")
                    _write_state({
                        "status": "pending",
                        "session_id": session_id,
                        "step_number": step_count,
                        "instruction": retry_prompt,
                        "schema": schema,
                        "response": None,
                        "error": None,
                        "python_pid": pid,
                        "cwd": cwd,
                        "transcript_lines": None,
                    })
                    response_text = await _wait_for_response(step_count)
                    resp_len = len(response_text) if response_text else 0
                    _log(f"Step {step_count}: retry response ({resp_len}b): {(response_text or '')[:120]!r}")
                else:
                    # Final attempt failed
                    if schema_strict:
                        _log(f"Step {step_count}: FATAL - 3 JSON parse failures, raising ValueError")
                        raise ValueError(
                            f"[auto] Step {step_count}: JSON parse failed after 3 attempts. "
                            f"Response was: {response_text[:200]}"
                        )
                    _log(f"Step {step_count}: WARNING - 3 JSON parse failures, returning nulls (strict=False)")
                    result = {k: None for k in schema}

        result_preview = json.dumps(result)[:100]
        _log(f"Step {step_count} result: {result_preview}")
        return result

    def _handle_sigterm(signum, frame):
        raise SystemExit("Received SIGTERM")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        await program_fn(step)
        _log(f"Program complete ({step_count} steps)")
    except SystemExit as e:
        _log(f"Program terminated: {e}")
        # Don't write error state for clean termination (SIGTERM)
        raise
    except Exception as e:
        import traceback
        _log(f"Program CRASHED: {type(e).__name__}: {e}")
        _log(f"Traceback:\n{traceback.format_exc()}")
        _write_state({
            "status": "error",
            "session_id": session_id,
            "step_number": step_count,
            "instruction": None,
            "schema": None,
            "response": None,
            "error": f"{type(e).__name__}: {e}",
            "python_pid": pid,
            "cwd": cwd,
        })
        raise
    else:
        _log("Writing done state")
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


async def run_program_v2(program_fn):
    """Execute an auto program using the Auto orchestration object."""
    from auto.core import Auto

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pid = os.getpid()

    import datetime
    print(f"\n{'='*60}", flush=True)
    _log(f"{datetime.datetime.now().isoformat()} Starting v2 (PID {pid})")

    # Use run dir from CLI if provided, otherwise create new one
    run_dir_env = os.environ.get("AUTO_RUN_DIR")
    if run_dir_env:
        from pathlib import Path
        auto = Auto(session_id=session_id, run_dir=Path(run_dir_env))
    else:
        auto = Auto(session_id=session_id)
    _log(f"Run dir: {auto.run_dir}")

    def _handle_sigterm(signum, frame):
        raise SystemExit("Received SIGTERM")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        await program_fn(auto)
        _log(f"Program complete ({auto._step_count} steps)")
        # Write done state so the hook exits cleanly
        from auto.run_folder import write_state
        _log("Writing done state")
        write_state(auto._self_state_path, {
            "name": "self",
            "status": "done",
            "session_id": session_id,
            "step_number": auto._step_count,
            "instruction": None,
            "schema": None,
            "response": None,
            "error": None,
            "pid": pid,
            "cwd": str(auto._project_root),
        })
    except SystemExit as e:
        _log(f"Program terminated: {e}")
        raise
    except Exception as e:
        import traceback
        _log(f"Program CRASHED: {type(e).__name__}: {e}")
        _log(f"Traceback:\n{traceback.format_exc()}")
        raise
    finally:
        auto.cleanup()
