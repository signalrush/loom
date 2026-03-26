# Orchestration Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `step()` primitive with a three-function API (`auto.remind`, `auto.task`, `auto.agent`) that supports multi-agent orchestration via `claude -p` subprocesses.

**Architecture:** The `auto` object wraps the existing stop-hook IPC for `remind()` (self-messaging) and adds `claude -p --resume` subprocess management for `task()` (delegation). Per-run folders in `~/.auto/run-{ts}-{pid}/` (global, not project-local) replace the current flat file layout. The stop hook reads from `$HOME/.auto/latest/self.json` instead of `.claude/auto-loop.json`. State files never pollute the project directory.

**Tech Stack:** Python 3.10+ stdlib only (asyncio, subprocess, json, tempfile). `claude` CLI for agent subprocesses. bash + jq for stop hook.

**Spec:** `docs/superpowers/specs/2026-03-26-orchestration-language-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/auto/core.py` | Create | `Auto` class with `remind()`, `task()`, `agent()` |
| `src/auto/agents.py` | Create | `AgentHandle` class — manages `claude -p` subprocess per agent |
| `src/auto/run_folder.py` | Create | Run folder creation, symlink, state file I/O |
| `src/auto/step.py` | Modify | Extract shared logic (JSON extraction, polling), keep `run_program()` as compat wrapper |
| `src/auto/cli.py` | Modify | New file layout paths, `auto-run log <agent>` arg, status shows all agents |
| `src/auto/hooks/stop-hook.sh` | Modify | Read from `$HOME/.auto/latest/self.json` |
| `src/auto/__init__.py` | Modify | Export new API |
| `tests/test_core.py` | Create | Tests for `Auto` class |
| `tests/test_agents.py` | Create | Tests for `AgentHandle` and `claude -p` subprocess management |
| `tests/test_run_folder.py` | Create | Tests for run folder creation, symlink, state I/O |
| `tests/test_cli_v2.py` | Create | Tests for updated CLI |

---

## Phase 1: Run Folder + Auto Object + remind()

Refactor existing `step()` into the new API and file layout. No multi-agent yet — just `remind()` working end-to-end.

### Task 1: Run Folder Module

**Files:**
- Create: `src/auto/run_folder.py`
- Test: `tests/test_run_folder.py`

- [ ] **Step 1: Write failing tests for run folder creation**

```python
# tests/test_run_folder.py
import os
import json
import time
from pathlib import Path

from auto.run_folder import create_run_folder, read_state, write_state


class TestRunFolder:
    def test_create_run_folder_creates_structure(self, tmp_path):
        """Run folder has correct structure with logs/ subdir."""
        run_dir = create_run_folder(tmp_path / ".auto")
        assert run_dir.exists()
        assert (run_dir / "logs").is_dir()
        assert run_dir.name.startswith("run-")
        # Name contains timestamp and PID
        parts = run_dir.name.split("-")
        assert len(parts) >= 4  # run-YYYYMMDD-HHMMSS-PID

    def test_create_run_folder_creates_latest_symlink(self, tmp_path):
        """latest symlink points to the new run folder."""
        auto_dir = tmp_path / ".auto"
        run_dir = create_run_folder(auto_dir)
        latest = auto_dir / "latest"
        assert latest.is_symlink()
        assert latest.resolve() == run_dir.resolve()

    def test_create_run_folder_updates_latest_on_second_run(self, tmp_path):
        """Second run atomically replaces the latest symlink."""
        auto_dir = tmp_path / ".auto"
        run1 = create_run_folder(auto_dir)
        run2 = create_run_folder(auto_dir)
        latest = auto_dir / "latest"
        assert latest.resolve() == run2.resolve()
        assert run1.exists()  # old run still exists

    def test_write_state_creates_file(self, tmp_path):
        """write_state creates a JSON file atomically."""
        state_path = tmp_path / "self.json"
        data = {"status": "pending", "step_number": 1, "instruction": "do X"}
        write_state(state_path, data)
        assert state_path.exists()
        loaded = json.loads(state_path.read_text())
        assert loaded["status"] == "pending"
        assert "updated_at" in loaded

    def test_read_state_returns_none_on_missing(self, tmp_path):
        """read_state returns None if file doesn't exist."""
        result = read_state(tmp_path / "missing.json")
        assert result is None

    def test_read_state_returns_none_on_invalid_json(self, tmp_path):
        """read_state returns None on corrupt JSON."""
        bad = tmp_path / "bad.json"
        bad.write_text("not json{{{")
        assert read_state(bad) is None

    def test_write_state_is_atomic(self, tmp_path):
        """write_state uses temp file + rename."""
        state_path = tmp_path / "state.json"
        write_state(state_path, {"status": "pending"})
        # No leftover temp files
        temps = list(tmp_path.glob("*.tmp"))
        assert len(temps) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_folder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto.run_folder'`

- [ ] **Step 3: Implement run_folder.py**

```python
# src/auto/run_folder.py
"""Run folder management for auto programs.

Each program run gets its own folder under ~/.auto/ (global, not project-local):
  ~/.auto/run-YYYYMMDD-HHMMSS-PID/
    self.json
    logs/
  ~/.auto/latest -> run-YYYYMMDD-HHMMSS-PID/
"""

import json
import os
import tempfile
import time
from pathlib import Path


def create_run_folder(auto_dir: Path) -> Path:
    """Create a timestamped run folder with logs/ subdir and latest symlink.

    Args:
        auto_dir: The ~/.auto/ directory.

    Returns:
        Path to the created run folder.
    """
    auto_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d-%H%M%S")
    pid = os.getpid()
    run_name = f"run-{ts}-{pid}"
    run_dir = auto_dir / run_name
    run_dir.mkdir()
    (run_dir / "logs").mkdir()

    # Atomic symlink update
    latest = auto_dir / "latest"
    tmp_link = auto_dir / f".latest-{pid}.tmp"
    try:
        os.symlink(run_name, tmp_link)
        os.rename(tmp_link, latest)
    except OSError:
        try:
            os.unlink(latest)
        except FileNotFoundError:
            pass
        os.symlink(run_name, latest)

    return run_dir


def write_state(path: Path, data: dict) -> None:
    """Write state file atomically with updated_at timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    fd, temp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".state-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def read_state(path: Path) -> dict | None:
    """Read state file. Returns None if missing or invalid."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_run_folder.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auto/run_folder.py tests/test_run_folder.py
git commit -m "feat: add run_folder module for per-run directory management"
```

---

### Task 2: Auto Class with remind()

**Files:**
- Create: `src/auto/core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Write failing tests for Auto class and remind()**

```python
# tests/test_core.py
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from auto.core import Auto


class TestAutoInit:
    def test_auto_creates_run_folder(self, tmp_path):
        """Auto() creates a run folder in ~/.auto/."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        assert auto.run_dir.exists()
        assert (auto.run_dir / "logs").is_dir()
        assert (tmp_path / ".auto" / "latest").is_symlink()

    def test_auto_self_state_path(self, tmp_path):
        """Auto stores self state in self.json."""
        auto = Auto(project_root=tmp_path)
        assert auto._self_state_path == auto.run_dir / "self.json"


class TestAutoAgent:
    def test_agent_declaration(self, tmp_path):
        """auto.agent() stores config."""
        auto = Auto(project_root=tmp_path)
        auto.agent("coder", cwd="/app")
        assert "coder" in auto._agents
        assert auto._agents["coder"]["cwd"] == "/app"

    def test_agent_redeclaration_is_noop(self, tmp_path):
        """Second auto.agent() call with same name is ignored."""
        auto = Auto(project_root=tmp_path)
        auto.agent("coder", cwd="/app")
        auto.agent("coder", cwd="/other")
        assert auto._agents["coder"]["cwd"] == "/app"

    def test_agent_default_cwd(self, tmp_path):
        """Agent without cwd= uses project root."""
        auto = Auto(project_root=tmp_path)
        auto.agent("helper")
        assert auto._agents["helper"]["cwd"] == str(tmp_path)


class TestAutoRemind:
    def test_remind_writes_pending_state(self, tmp_path):
        """remind() writes pending status to self.json."""
        auto = Auto(project_root=tmp_path)

        # Mock _wait_for_response to return immediately
        async def mock_wait(step_num):
            return "test response"

        with patch.object(auto, "_wait_for_response", side_effect=mock_wait):
            result = asyncio.run(auto.remind("do something"))

        assert result == "test response"
        state = json.loads((auto.run_dir / "self.json").read_text())
        # After completion, state should reflect the last write
        assert state["step_number"] == 1

    def test_remind_with_schema_returns_dict(self, tmp_path):
        """remind() with schema parses JSON response."""
        auto = Auto(project_root=tmp_path)

        async def mock_wait(step_num):
            return '{"score": 0.95}'

        with patch.object(auto, "_wait_for_response", side_effect=mock_wait):
            result = asyncio.run(
                auto.remind("analyze", schema={"score": "float"})
            )

        assert isinstance(result, dict)
        assert result["score"] == 0.95

    def test_remind_increments_step_count(self, tmp_path):
        """Each remind() call increments step_number."""
        auto = Auto(project_root=tmp_path)
        call_count = 0

        async def mock_wait(step_num):
            nonlocal call_count
            call_count += 1
            return f"response {call_count}"

        with patch.object(auto, "_wait_for_response", side_effect=mock_wait):
            asyncio.run(auto.remind("first"))
            asyncio.run(auto.remind("second"))

        assert auto._step_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto.core'`

- [ ] **Step 3: Implement core.py**

```python
# src/auto/core.py
"""Auto orchestration object.

Provides remind(), task(), and agent() for controlling Claude Code sessions.
"""

import asyncio
import json
import os
import signal
import time
from pathlib import Path

from auto.run_folder import create_run_folder, write_state, read_state
from auto.step import _extract_json, _log, POLL_INTERVAL


class Auto:
    """Orchestration object passed to auto programs as `async def main(auto):`."""

    def __init__(self, project_root: Path = None, session_id: str = "",
                 auto_dir: Path = None):
        if project_root is None:
            project_root = Path.cwd()
        self._project_root = Path(project_root)
        self._session_id = session_id
        self._pid = os.getpid()
        self._cwd = str(self._project_root.resolve())

        # Create run folder in global ~/.auto/ (or override for tests)
        if auto_dir is None:
            auto_dir = Path.home() / ".auto"
        self.run_dir = create_run_folder(auto_dir)
        self._self_state_path = self.run_dir / "self.json"

        # Agent registry: name -> {"cwd": str, "session_id": str|None}
        self._agents: dict[str, dict] = {}

        # Step counter for remind()
        self._step_count = 0

    def agent(self, name: str, cwd: str = None) -> None:
        """Declare an agent. First declaration wins (redeclaration is no-op)."""
        if name in self._agents:
            return
        self._agents[name] = {
            "cwd": cwd or self._cwd,
            "session_id": None,
        }

    async def remind(self, instruction: str, schema: dict = None,
                     timeout: int = None) -> str | dict:
        """Send yourself a message via stop-hook IPC.

        Args:
            instruction: What to do.
            schema: Optional JSON schema for structured output.
            timeout: Seconds before TimeoutError. None = wait forever.

        Returns:
            str or dict (if schema provided).
        """
        self._step_count += 1
        step_num = self._step_count
        _log(f"Step {step_num}: {instruction[:80]}...")

        # Write pending state
        write_state(self._self_state_path, {
            "name": "self",
            "status": "pending",
            "session_id": self._session_id,
            "step_number": step_num,
            "instruction": instruction,
            "schema": schema,
            "response": None,
            "error": None,
            "pid": self._pid,
            "cwd": self._cwd,
            "transcript_lines": None,
        })

        # Wait for response
        _log(f"Step {step_num}: waiting for response...")
        if timeout is not None:
            response_text = await asyncio.wait_for(
                self._wait_for_response(step_num),
                timeout=timeout,
            )
        else:
            response_text = await self._wait_for_response(step_num)

        _log(f"Step {step_num}: got response ({len(response_text or '')}b)")

        if schema is None:
            return response_text

        # Parse JSON with retries
        return await self._parse_with_retries(
            response_text, schema, step_num
        )

    async def task(self, instruction: str, to: str, schema: dict = None,
                   timeout: int = None) -> str | dict:
        """Assign work to another agent via claude -p subprocess.

        Args:
            instruction: What to do.
            to: Agent name.
            schema: Optional JSON schema for structured output.
            timeout: Seconds before TimeoutError. None = wait forever.

        Returns:
            str or dict (if schema provided).
        """
        # Ensure agent is declared
        if to not in self._agents:
            self.agent(to)

        # Placeholder — implemented in Task 4
        raise NotImplementedError("task() will be implemented in Phase 2")

    async def _wait_for_response(self, step_number: int) -> str:
        """Poll self.json for responded status matching step_number."""
        start = time.monotonic()
        poll_count = 0
        last_status = None

        while True:
            state = read_state(self._self_state_path)

            if state is None:
                raise RuntimeError("State file disappeared")

            cur_status = state.get("status")
            cur_step = state.get("step_number")

            # Log status changes
            if cur_status != last_status:
                elapsed = time.monotonic() - start
                _log(f"_wait({step_number}): status={cur_status}, "
                     f"step={cur_step} [{elapsed:.1f}s]")
                last_status = cur_status
            elif poll_count > 0 and poll_count % 100 == 0:
                elapsed = time.monotonic() - start
                _log(f"_wait({step_number}): HEARTBEAT {elapsed:.0f}s")

            if cur_status == "responded" and cur_step == step_number:
                return state.get("response", "")

            if cur_status == "error":
                raise RuntimeError(f"Hook error: {state.get('error')}")

            poll_count += 1
            await asyncio.sleep(POLL_INTERVAL)

    async def _parse_with_retries(self, response_text: str, schema: dict,
                                  step_num: int) -> dict:
        """Parse JSON from response, retry up to 3 times."""
        for attempt in range(3):
            try:
                return _extract_json(response_text)
            except ValueError:
                if attempt < 2:
                    self._step_count += 1
                    retry_prompt = (
                        f"Your previous response was not valid JSON. "
                        f"Respond with a JSON object with these keys: "
                        f"{json.dumps(schema)}"
                    )
                    _log(f"Step {self._step_count}: JSON retry {attempt+1}/2")
                    write_state(self._self_state_path, {
                        "name": "self",
                        "status": "pending",
                        "session_id": self._session_id,
                        "step_number": self._step_count,
                        "instruction": retry_prompt,
                        "schema": schema,
                        "response": None,
                        "error": None,
                        "pid": self._pid,
                        "cwd": self._cwd,
                        "transcript_lines": None,
                    })
                    response_text = await self._wait_for_response(
                        self._step_count
                    )
                else:
                    raise ValueError(
                        f"JSON parse failed after 3 attempts: "
                        f"{response_text[:200]}"
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_core.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auto/core.py tests/test_core.py
git commit -m "feat: add Auto class with remind() and agent()"
```

---

### Task 3: Update stop hook + cli for new file layout

**Files:**
- Modify: `src/auto/hooks/stop-hook.sh:6` (state file path)
- Modify: `src/auto/cli.py` (run folder creation, log paths, status output)
- Test: `tests/test_cli_v2.py`

- [ ] **Step 1: Write failing tests for updated CLI**

```python
# tests/test_cli_v2.py
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import auto.cli as cli_mod


def _mock_popen(pid=12345):
    mock = MagicMock()
    mock.pid = pid
    return mock


class TestRunFolderIntegration:
    """Test that CLI creates the new run folder structure."""

    def setup_method(self):
        """Store original module constants."""
        self._orig_auto_dir = getattr(cli_mod, "AUTO_DIR", None)

    def test_start_creates_run_folder(self, tmp_path, monkeypatch):
        """auto-run program.py creates ~/.auto/run-{ts}-{pid}/."""
        monkeypatch.chdir(tmp_path)
        prog = tmp_path / "prog.py"
        prog.write_text("async def main(auto): pass\n")
        (tmp_path / ".claude").mkdir()

        monkeypatch.setattr(cli_mod, "AUTO_DIR", str(tmp_path / ".auto"))
        monkeypatch.setattr(cli_mod, "PID_FILE", str(tmp_path / ".auto" / "auto.pid"))

        with patch.object(subprocess, "Popen", return_value=_mock_popen(111)):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(str(prog))

        auto_dir = tmp_path / ".auto"
        assert auto_dir.exists()
        latest = auto_dir / "latest"
        assert latest.is_symlink()


class TestStatusMultiAgent:
    """Test that status shows all agents."""

    def test_status_shows_agent_states(self, tmp_path, capsys, monkeypatch):
        """auto-run status reads all .json files in latest/."""
        run_dir = tmp_path / ".auto" / "run-20260326-150000-99999"
        run_dir.mkdir(parents=True)
        (run_dir / "logs").mkdir()

        # Write agent state files
        for name, status, step, instr in [
            ("self", "pending", 3, "check CI"),
            ("coder", "running", 1, "fix bug"),
        ]:
            (run_dir / f"{name}.json").write_text(json.dumps({
                "name": name,
                "status": status,
                "step_number": step,
                "last_instruction": instr,
                "updated_at": "2026-03-26T15:00:00Z",
            }))

        # Create latest symlink
        latest = tmp_path / ".auto" / "latest"
        os.symlink("run-20260326-150000-99999", latest)

        monkeypatch.setattr(cli_mod, "AUTO_DIR", str(tmp_path / ".auto"))
        monkeypatch.setattr(cli_mod, "PID_FILE", str(tmp_path / ".auto" / "auto.pid"))

        cli_mod._show_status()
        output = capsys.readouterr().out
        assert "self" in output
        assert "coder" in output


class TestLogWithAgentName:
    """Test auto-run log <agent> support."""

    def test_tail_log_defaults_to_self(self, tmp_path, monkeypatch):
        """auto-run log with no args tails self.log."""
        run_dir = tmp_path / ".auto" / "run-test"
        (run_dir / "logs").mkdir(parents=True)
        (run_dir / "logs" / "self.log").write_text("hello\n")
        os.symlink("run-test", tmp_path / ".auto" / "latest")

        monkeypatch.setattr(cli_mod, "AUTO_DIR", str(tmp_path / ".auto"))
        # Just test the path resolution, not the actual tail
        log_path = Path(cli_mod.AUTO_DIR) / "latest" / "logs" / "self.log"
        assert log_path.exists()

    def test_tail_log_agent_name(self, tmp_path, monkeypatch):
        """auto-run log coder tails coder.log."""
        run_dir = tmp_path / ".auto" / "run-test"
        (run_dir / "logs").mkdir(parents=True)
        (run_dir / "logs" / "coder.log").write_text("agent log\n")
        os.symlink("run-test", tmp_path / ".auto" / "latest")

        monkeypatch.setattr(cli_mod, "AUTO_DIR", str(tmp_path / ".auto"))
        log_path = Path(cli_mod.AUTO_DIR) / "latest" / "logs" / "coder.log"
        assert log_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_v2.py -v`
Expected: FAIL (AUTO_DIR constant doesn't exist yet)

- [ ] **Step 3: Update cli.py constants and _start_program**

Update `src/auto/cli.py`:

1. Replace constants at top:
```python
AUTO_DIR = os.path.join(str(Path.home()), ".auto")
PID_FILE = os.path.join(str(Path.home()), ".auto", "auto.pid")
```

2. In `_start_program()`: replace the log file creation with run folder creation. Use `create_run_folder()` to make the run directory, write logs to `run_dir/logs/self.log`.

3. In `_show_status()`: read all `*.json` files from `AUTO_DIR/latest/` and display each agent's status.

4. In `_tail_log()`: accept optional agent name argument. Default to `self.log`. Resolve path via `AUTO_DIR/latest/logs/{name}.log`.

5. In `main()`: parse `auto-run log <agent>` by passing remaining args.

- [ ] **Step 4: Update stop-hook.sh state file path**

In `src/auto/hooks/stop-hook.sh`, change line 6:
```bash
# Old:
STATE_FILE=".claude/auto-loop.json"
# New:
STATE_FILE="$HOME/.auto/latest/self.json"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli_v2.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run existing tests to check for regressions**

Run: `pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_e2e_program.py`
Expected: All tests PASS (may need to update some tests that reference old paths)

- [ ] **Step 7: Commit**

```bash
git add src/auto/cli.py src/auto/hooks/stop-hook.sh tests/test_cli_v2.py
git commit -m "feat: migrate CLI and stop hook to per-run folder layout"
```

---

### Task 4: Wire up run_program() to use Auto object

**Files:**
- Modify: `src/auto/step.py` (add `run_program_v2` that creates `Auto` and passes it)
- Modify: `src/auto/__init__.py` (export new API)
- Test: manual verification with existing `program.py`

- [ ] **Step 1: Add run_program_v2 to step.py**

Add to the end of `src/auto/step.py`:

```python
async def run_program_v2(program_fn):
    """Execute an auto program using the Auto orchestration object.

    This is the v2 entry point that passes an Auto object instead of a
    bare step() function. The Auto object provides remind(), task(), and agent().

    Existing programs using `async def main(step)` continue to work via
    the original run_program().
    """
    from auto.core import Auto

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    pid = os.getpid()

    import datetime
    print(f"\n{'='*60}", flush=True)
    _log(f"{datetime.datetime.now().isoformat()} Starting v2 (PID {pid})")

    auto = Auto(session_id=session_id)
    _log(f"Run dir: {auto.run_dir}")

    def _handle_sigterm(signum, frame):
        raise SystemExit("Received SIGTERM")

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        await program_fn(auto)
        _log(f"Program complete ({auto._step_count} steps)")
    except SystemExit as e:
        _log(f"Program terminated: {e}")
        raise
    except Exception as e:
        import traceback
        _log(f"Program CRASHED: {type(e).__name__}: {e}")
        _log(f"Traceback:\n{traceback.format_exc()}")
        raise
```

- [ ] **Step 2: Update __init__.py exports**

```python
# src/auto/__init__.py
from auto.step import run_program, run_program_v2, _extract_json
from auto.core import Auto
from auto import state
```

- [ ] **Step 3: Update cli.py to detect program signature and use v2**

In `_start_program()`, update the inline Python script to detect whether the program's `main` function accepts an `Auto` object (by parameter name) and use `run_program_v2` accordingly:

```python
# In the Popen -c script:
"""
import asyncio, importlib.util, sys, os, inspect
os.chdir({cwd!r})
sys.path.insert(0, os.getcwd())
spec = importlib.util.spec_from_file_location('program', {program_path!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Detect v1 (step) vs v2 (auto) by parameter name
params = list(inspect.signature(mod.main).parameters.keys())
if params and params[0] == 'auto':
    from auto.step import run_program_v2
    asyncio.run(run_program_v2(mod.main))
else:
    from auto.step import run_program
    asyncio.run(run_program(mod.main))
"""
```

- [ ] **Step 4: Test with a v2 program**

Create `program_v2.py`:
```python
async def main(auto):
    answer = await auto.remind("What is 2 + 2? Reply with just the number.")
    print(f"Claude said: {answer}")
```

Run: `auto-run program_v2.py`
Verify: logs appear in `~/.auto/latest/logs/self.log`, state in `~/.auto/latest/self.json`

- [ ] **Step 5: Test backward compat with v1 program**

Run: `auto-run program.py` (existing program using `step`)
Verify: still works with the old `run_program()` path

- [ ] **Step 6: Commit**

```bash
git add src/auto/step.py src/auto/__init__.py src/auto/cli.py program_v2.py
git commit -m "feat: wire Auto object into run_program_v2 with v1 backward compat"
```

---

## Phase 2: Multi-Agent (task)

### Task 5: AgentHandle — claude -p subprocess management

**Files:**
- Create: `src/auto/agents.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write failing tests for AgentHandle**

```python
# tests/test_agents.py
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from auto.agents import AgentHandle


def _mock_completed_process(result_text="done", session_id="test-uuid-123"):
    """Create a mock CompletedProcess mimicking claude -p --output-format json."""
    output = json.dumps({"result": result_text, "session_id": session_id})
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    mock.stderr = ""
    return mock


class TestAgentHandle:
    def test_first_call_stores_session_id(self, tmp_path):
        """First task() call captures session_id from claude output."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("fixed it", "uuid-abc")):
            result = asyncio.run(agent.run("fix the bug"))

        assert result == "fixed it"
        assert agent.session_id == "uuid-abc"
        # Session ID persisted to state file
        state = json.loads(state_path.read_text())
        assert state["session_id"] == "uuid-abc"

    def test_second_call_uses_resume(self, tmp_path):
        """Subsequent calls use --resume with stored session_id."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")
        agent.session_id = "uuid-abc"

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("tested")) as mock_run:
            asyncio.run(agent.run("add tests"))

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert "uuid-abc" in cmd

    def test_first_call_no_resume(self, tmp_path):
        """First call (no session_id) does not use --resume."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process()) as mock_run:
            asyncio.run(agent.run("do something"))

        cmd = mock_run.call_args[0][0]
        assert "--resume" not in cmd

    def test_nonzero_exit_raises(self, tmp_path):
        """Non-zero exit from claude -p raises RuntimeError."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")

        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        mock.stderr = "error: something broke"

        with patch("auto.agents.subprocess.run", return_value=mock):
            try:
                asyncio.run(agent.run("do something"))
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "something broke" in str(e)

    def test_malformed_json_raises(self, tmp_path):
        """Malformed JSON output raises RuntimeError."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")

        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "not json{{"
        mock.stderr = ""

        with patch("auto.agents.subprocess.run", return_value=mock):
            try:
                asyncio.run(agent.run("do something"))
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "JSON" in str(e) or "parse" in str(e).lower()

    def test_state_file_written_on_each_call(self, tmp_path):
        """State file is updated with status and instruction on each call."""
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("ok")):
            asyncio.run(agent.run("do task"))

        state = json.loads(state_path.read_text())
        assert state["name"] == "coder"
        assert state["last_instruction"] == "do task"
        assert state["status"] == "idle"
        assert "updated_at" in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'auto.agents'`

- [ ] **Step 3: Implement agents.py**

```python
# src/auto/agents.py
"""Agent subprocess management via claude -p."""

import json
import os
import subprocess
import time
from pathlib import Path

from auto.run_folder import write_state, read_state
from auto.step import _log


class AgentHandle:
    """Manages a named claude -p session."""

    def __init__(self, name: str, cwd: str, state_path: Path,
                 log_path: Path):
        self.name = name
        self.cwd = cwd
        self.state_path = state_path
        self.log_path = log_path
        self.session_id: str | None = None
        self.step_count = 0

        # Restore session_id from state file if exists
        existing = read_state(state_path)
        if existing and existing.get("session_id"):
            self.session_id = existing["session_id"]

    async def run(self, instruction: str, timeout: int = None) -> str:
        """Execute instruction via claude -p and return the result text.

        Args:
            instruction: What to do.
            timeout: Seconds before TimeoutError.

        Returns:
            Response text from the agent.

        Raises:
            RuntimeError: On subprocess failure or malformed output.
            TimeoutError: If timeout exceeded.
        """
        self.step_count += 1
        _log(f"[{self.name}] Step {self.step_count}: {instruction[:80]}...")

        # Write running state
        write_state(self.state_path, {
            "name": self.name,
            "session_id": self.session_id,
            "status": "running",
            "step_number": self.step_count,
            "last_instruction": instruction,
            "cwd": self.cwd,
            "pid": os.getpid(),
        })

        # Build command
        cmd = [
            "claude", "-p", instruction,
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]
        if self.session_id:
            cmd.extend(["--resume", self.session_id])

        _log(f"[{self.name}] Running: claude -p "
             f"{'--resume ' + self.session_id[:8] + '... ' if self.session_id else ''}"
             f"({len(instruction)}b instruction)")

        # Run subprocess
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=self.cwd,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            write_state(self.state_path, {
                "name": self.name,
                "session_id": self.session_id,
                "status": "error",
                "step_number": self.step_count,
                "last_instruction": instruction,
                "cwd": self.cwd,
                "pid": os.getpid(),
            })
            raise TimeoutError(
                f"Agent '{self.name}' timed out after {timeout}s"
            )

        # Append output to log
        with open(self.log_path, "a") as f:
            f.write(f"--- Step {self.step_count} ---\n")
            if result.stdout:
                f.write(result.stdout + "\n")
            if result.stderr:
                f.write(f"STDERR: {result.stderr}\n")

        # Check exit code
        if result.returncode != 0:
            write_state(self.state_path, {
                "name": self.name,
                "session_id": self.session_id,
                "status": "error",
                "step_number": self.step_count,
                "last_instruction": instruction,
                "cwd": self.cwd,
                "pid": os.getpid(),
            })
            raise RuntimeError(
                f"Agent '{self.name}' failed (exit {result.returncode}): "
                f"{result.stderr[:500]}"
            )

        # Parse JSON output
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Agent '{self.name}' returned malformed JSON: {e}. "
                f"Output: {result.stdout[:200]}"
            )

        # Extract result and session_id
        response_text = output.get("result", "")
        new_session_id = output.get("session_id")
        if new_session_id:
            self.session_id = new_session_id

        _log(f"[{self.name}] Step {self.step_count}: done ({len(response_text)}b)")

        # Write idle state with session_id
        write_state(self.state_path, {
            "name": self.name,
            "session_id": self.session_id,
            "status": "idle",
            "step_number": self.step_count,
            "last_instruction": instruction,
            "cwd": self.cwd,
            "pid": os.getpid(),
        })

        return response_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auto/agents.py tests/test_agents.py
git commit -m "feat: add AgentHandle for claude -p subprocess management"
```

---

### Task 6: Wire task() into Auto class

**Files:**
- Modify: `src/auto/core.py` (implement `task()` using `AgentHandle`)
- Test: `tests/test_core.py` (add task tests)

- [ ] **Step 1: Add failing tests for task()**

Append to `tests/test_core.py`:

```python
class TestAutoTask:
    def test_task_creates_agent_implicitly(self, tmp_path):
        """task(to="helper") without prior agent() creates one."""
        auto = Auto(project_root=tmp_path)

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("done", "uuid-1")):
            result = asyncio.run(auto.task("do X", to="helper"))

        assert result == "done"
        assert "helper" in auto._agents

    def test_task_uses_declared_cwd(self, tmp_path):
        """task() uses the cwd from agent() declaration."""
        auto = Auto(project_root=tmp_path)
        auto.agent("coder", cwd="/custom/path")

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("fixed")) as mock_run:
            asyncio.run(auto.task("fix bug", to="coder"))

        # Verify cwd was passed to subprocess
        assert mock_run.call_args[1]["cwd"] == "/custom/path"

    def test_task_with_schema_parses_json(self, tmp_path):
        """task() with schema extracts JSON from agent response."""
        auto = Auto(project_root=tmp_path)

        result_text = '{"approved": true, "reason": "looks good"}'
        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process(result_text, "uuid-1")):
            result = asyncio.run(
                auto.task("review", to="reviewer",
                          schema={"approved": "bool", "reason": "str"})
            )

        assert result["approved"] is True

    def test_task_persists_session_across_calls(self, tmp_path):
        """Second task() to same agent resumes the session."""
        auto = Auto(project_root=tmp_path)

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("r1", "uuid-first")):
            asyncio.run(auto.task("first", to="coder"))

        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("r2", "uuid-first")) as mock_run:
            asyncio.run(auto.task("second", to="coder"))

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert "uuid-first" in cmd
```

Add this import at the top of the file:
```python
from auto.agents import AgentHandle

def _mock_completed_process(result_text="done", session_id="test-uuid"):
    output = json.dumps({"result": result_text, "session_id": session_id})
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    mock.stderr = ""
    return mock
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_core.py::TestAutoTask -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement task() in core.py**

Replace the placeholder `task()` in `src/auto/core.py`:

```python
async def task(self, instruction: str, to: str, schema: dict = None,
               timeout: int = None) -> str | dict:
    """Assign work to another agent via claude -p subprocess."""
    # Ensure agent is declared
    if to not in self._agents:
        self.agent(to)

    # Get or create AgentHandle
    agent_config = self._agents[to]
    if "_handle" not in agent_config:
        from auto.agents import AgentHandle
        agent_config["_handle"] = AgentHandle(
            name=to,
            cwd=agent_config["cwd"],
            state_path=self.run_dir / f"{to}.json",
            log_path=self.run_dir / "logs" / f"{to}.log",
        )

    handle = agent_config["_handle"]

    # Build instruction with schema guidance
    full_instruction = instruction
    if schema:
        schema_desc = json.dumps(schema)
        full_instruction += (
            f"\n\nRespond with a JSON object with these keys and types: "
            f"{schema_desc}"
        )

    response_text = await handle.run(full_instruction, timeout=timeout)

    if schema is None:
        return response_text

    # Parse JSON
    try:
        return _extract_json(response_text)
    except ValueError:
        raise ValueError(
            f"Agent '{to}' response was not valid JSON: "
            f"{response_text[:200]}"
        )
```

Add this import at the top of `core.py`:
```python
from auto.step import _extract_json
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/test_core.py tests/test_agents.py tests/test_run_folder.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auto/core.py tests/test_core.py
git commit -m "feat: implement task() for multi-agent delegation via claude -p"
```

---

### Task 7: Cleanup handler + update __init__.py

**Files:**
- Modify: `src/auto/core.py` (add atexit cleanup)
- Modify: `src/auto/__init__.py` (clean exports)
- Test: `tests/test_core.py` (add cleanup test)

- [ ] **Step 1: Add failing test for cleanup**

Append to `tests/test_core.py`:

```python
class TestAutoCleanup:
    def test_cleanup_kills_agent_handles(self, tmp_path):
        """Auto.cleanup() terminates tracked agent subprocesses."""
        auto = Auto(project_root=tmp_path)
        auto.agent("coder")

        # Simulate an active agent handle
        from auto.agents import AgentHandle
        handle = AgentHandle("coder", cwd=str(tmp_path),
                            state_path=auto.run_dir / "coder.json",
                            log_path=auto.run_dir / "logs" / "coder.log")
        auto._agents["coder"]["_handle"] = handle

        # Cleanup should not raise even with no active subprocesses
        auto.cleanup()
```

- [ ] **Step 2: Implement cleanup in core.py**

Add to the `Auto` class:

```python
def cleanup(self) -> None:
    """Terminate all agent sessions. Called on program exit."""
    for name, config in self._agents.items():
        handle = config.get("_handle")
        if handle:
            _log(f"Cleaning up agent '{name}'")
            # AgentHandle uses subprocess.run (synchronous), so no
            # long-running processes to kill. Just update state.
            try:
                write_state(handle.state_path, {
                    "name": name,
                    "session_id": handle.session_id,
                    "status": "stopped",
                    "step_number": handle.step_count,
                    "last_instruction": "",
                    "cwd": handle.cwd,
                    "pid": os.getpid(),
                })
            except OSError:
                pass
```

Update `run_program_v2` in `step.py` to call cleanup:

```python
try:
    await program_fn(auto)
    _log(f"Program complete ({auto._step_count} steps)")
finally:
    auto.cleanup()
```

- [ ] **Step 3: Update __init__.py**

```python
# src/auto/__init__.py
from auto.step import run_program, run_program_v2, _extract_json
from auto.core import Auto
from auto import state
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/test_core.py tests/test_agents.py tests/test_run_folder.py tests/test_cli_v2.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/auto/core.py src/auto/step.py src/auto/__init__.py tests/test_core.py
git commit -m "feat: add cleanup handler and finalize exports"
```

---

### Task 8: End-to-end integration test

**Files:**
- Create: `tests/test_e2e_v2.py`

- [ ] **Step 1: Write e2e test for remind() flow**

```python
# tests/test_e2e_v2.py
"""End-to-end tests for the v2 Auto API.

These test the full flow without a live Claude session by mocking
the stop hook's response writing.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from threading import Thread

from auto.core import Auto


def _simulate_hook_response(state_path: Path, step_number: int,
                            response: str, delay: float = 0.2):
    """Simulate the stop hook writing a response after a delay."""
    def _write():
        time.sleep(delay)
        # Read current state
        with open(state_path) as f:
            state = json.load(f)
        # Write responded
        state["status"] = "responded"
        state["response"] = response
        with open(state_path, "w") as f:
            json.dump(state, f)

    Thread(target=_write, daemon=True).start()


class TestRemindE2E:
    def test_remind_full_cycle(self, tmp_path):
        """remind() writes pending, waits for responded, returns result."""
        auto = Auto(project_root=tmp_path)

        async def run():
            # Simulate hook responding after 200ms
            _simulate_hook_response(
                auto._self_state_path, 1, "the answer is 42"
            )
            result = await auto.remind("what is the answer?")
            assert result == "the answer is 42"

        asyncio.run(run())

    def test_remind_with_schema_e2e(self, tmp_path):
        """remind() with schema parses JSON from simulated response."""
        auto = Auto(project_root=tmp_path)

        async def run():
            _simulate_hook_response(
                auto._self_state_path, 1, '{"score": 0.95}'
            )
            result = await auto.remind(
                "rate it", schema={"score": "float"}
            )
            assert result["score"] == 0.95

        asyncio.run(run())

    def test_two_reminds_sequential(self, tmp_path):
        """Two sequential remind() calls with incrementing step numbers."""
        auto = Auto(project_root=tmp_path)

        async def run():
            _simulate_hook_response(auto._self_state_path, 1, "first")
            r1 = await auto.remind("step 1")
            assert r1 == "first"

            _simulate_hook_response(auto._self_state_path, 2, "second")
            r2 = await auto.remind("step 2")
            assert r2 == "second"

        asyncio.run(run())

    def test_remind_timeout(self, tmp_path):
        """remind() with timeout raises TimeoutError."""
        auto = Auto(project_root=tmp_path)

        async def run():
            # No hook response — should timeout
            try:
                await auto.remind("do something", timeout=0.5)
                assert False, "Should have raised TimeoutError"
            except (TimeoutError, asyncio.TimeoutError):
                pass

        asyncio.run(run())
```

- [ ] **Step 2: Run e2e tests**

Run: `pytest tests/test_e2e_v2.py -v`
Expected: All 4 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_v2.py
git commit -m "test: add e2e tests for Auto.remind() flow"
```

---

### Task 9: Update .gitignore and clean up

**Files:**
- Modify: `.gitignore`
- Modify: `program.py` (update to v2 API as example)

- [ ] **Step 1: Update .gitignore**

Add to `.gitignore`:
```
.auto/
```

- [ ] **Step 2: Update program.py to v2 API**

```python
"""Simple test program using the v2 Auto API."""

async def main(auto):
    answer = await auto.remind("What is 2 + 2? Reply with just the number.")
    print(f"Claude said: {answer}")

    answer2 = await auto.remind(f"You said {answer}. Now what is 10 * 10? Reply with just the number.")
    print(f"Claude said: {answer2}")

    print("Program complete!")
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_e2e_program.py`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add .gitignore program.py
git commit -m "chore: update gitignore for new run folder layout, update example to v2 API"
```

---

## Summary

| Task | Phase | What it builds |
|------|-------|---------------|
| 1 | 1 | `run_folder.py` — per-run directory + state I/O |
| 2 | 1 | `core.py` — `Auto` class with `remind()` + `agent()` |
| 3 | 1 | CLI + stop hook migration to new file layout |
| 4 | 1 | `run_program_v2()` wiring + v1 backward compat |
| 5 | 2 | `agents.py` — `AgentHandle` with `claude -p` subprocess |
| 6 | 2 | Wire `task()` into `Auto` class |
| 7 | 2 | Cleanup handler + final exports |
| 8 | 2 | End-to-end integration tests |
| 9 | 2 | Gitignore + example update |
