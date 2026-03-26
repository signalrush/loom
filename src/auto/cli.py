"""CLI entry point for auto-run."""

import json
import subprocess
import sys
import os
import signal
import time
from pathlib import Path


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("Usage: auto-run <command> [args...]")
        print()
        print("Commands:")
        print("    auto-run <program.py>   Start an auto program in background")
        print("    auto-run setup          Install stop hook into .claude/settings.local.json")
        print("    auto-run status         Show running state and recent logs")
        print("    auto-run log            Tail the auto.log file")
        print("    auto-run stop           Kill running program")
        print()
        print("Environment Variables:")
        print("    CLAUDE_CODE_SESSION_ID  Set automatically by Claude Code")
        sys.exit(0)

    command = sys.argv[1]

    if command == "setup":
        _setup_hook()
    elif command == "status":
        _show_status()
    elif command == "log":
        _tail_log()
    elif command == "stop":
        _stop_program()
    elif command.endswith(".py"):
        _start_program(command)
    else:
        print(f"Error: Unknown command '{command}'", file=sys.stderr)
        sys.exit(1)


PID_FILE = ".auto.pid"
LOG_FILE = "auto.log"

AUTO_SKILL_MARKER = "<!-- auto-agent -->"
AUTO_SKILL_CONTENT = """\
<!-- auto-agent -->
# Auto — step() loop

`auto-agent` is installed. Write a Python program that drives agent turns via `step()`.

## Usage

```python
# program.py
async def main(step):
    result = await step("Do something")                              # → str
    data = await step("Report metrics", schema={"loss": "float"})    # → dict
```

```bash
auto-run program.py   # start program, then type "go" in Claude Code
auto-run status       # monitor
auto-run stop         # kill
```

## step() API

- `step(instruction)` → `str`
- `step(instruction, schema={"key": "type"})` → `dict`
- `step(instruction, schema={...}, schema_strict=False)` → `dict` (nulls on parse failure)

Each `step()` is a full agent turn with all tool access. Context accumulates across steps.

## Patterns

```python
# Optimization loop
async def main(step):
    best = float("inf")
    for i in range(20):
        r = await step(f"Experiment {i}: beat {best}", schema={"loss": "float"})
        if r["loss"] < best:
            best = r["loss"]
        else:
            await step("Revert: git reset --hard HEAD~1")
        if (i + 1) % 5 == 0:
            await step("Reflect and adjust strategy")
```

## State tracking (optional)

```python
from auto import state
state.set("status", "running")
state.update({"step": i, "score": score})
```

Progress visible via `auto-run status` or `cat auto-state.json`.
<!-- /auto-agent -->
"""


def _install_skill():
    """Install auto skill instructions into .claude/CLAUDE.md."""
    claude_md = Path(".claude") / "CLAUDE.md"

    if claude_md.exists():
        content = claude_md.read_text()
        if AUTO_SKILL_MARKER in content:
            return
        new_content = content.rstrip() + "\n\n" + AUTO_SKILL_CONTENT
    else:
        new_content = AUTO_SKILL_CONTENT

    claude_md.parent.mkdir(exist_ok=True)
    claude_md.write_text(new_content)
    print(f"[auto] Skill instructions written to {claude_md.resolve()}")


def _setup_hook():
    """Install the auto stop hook into .claude/settings.local.json."""
    # Check dependencies
    for dep in ("jq", "git"):
        try:
            subprocess.run([dep, "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"Warning: '{dep}' not found. The stop hook requires jq and git.", file=sys.stderr)

    hooks_dir = Path(".claude")
    hooks_dir.mkdir(exist_ok=True)
    settings_file = hooks_dir / "settings.local.json"

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

    if settings_file.exists():
        with open(settings_file) as f:
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
                _install_skill()
                return

    stop_hooks.append({"hooks": [hook_entry]})

    with open(settings_file, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[auto] Installed stop hook: {hook_script_abs}")
    print(f"[auto] Config written to: {settings_file.resolve()}")

    _install_skill()


def _start_program(program_path):
    """Start an auto program as a background process."""
    if not os.path.isfile(program_path):
        print(f"Error: {program_path} not found", file=sys.stderr)
        sys.exit(1)

    # Auto-setup hook + skill if not already installed
    _setup_hook()

    # Check if already running
    if os.path.isfile(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Error: Auto program already running (PID {old_pid})", file=sys.stderr)
            print("Use 'auto-run stop' first", file=sys.stderr)
            sys.exit(1)
        except (ProcessLookupError, PermissionError):
            os.remove(PID_FILE)

    program_path = os.path.abspath(program_path)
    cwd = os.getcwd()  # project root -- must match hook's working directory

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    log_fh = open(LOG_FILE, "a")
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
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    log_fh.close()

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


def _show_status():
    print("=== Auto Status ===")

    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
        except (ValueError, OSError):
            print("Process: PID file corrupted")
            pid = None
        else:
            try:
                os.kill(pid, 0)
                print(f"Process: Running (PID {pid})")
            except (ProcessLookupError, PermissionError):
                print(f"Process: Not running (stale PID {pid})")
                os.remove(PID_FILE)
    else:
        print("Process: Not running")

    # Show state (always, even when process is dead — useful for post-mortem)
    print()
    print("=== State ===")
    ipc_state_file = ".claude/auto-loop.json"
    if os.path.isfile(ipc_state_file):
        with open(ipc_state_file) as f:
            print(f.read())
    elif os.path.isfile("auto-state.json"):
        with open("auto-state.json") as f:
            print(f.read())
    else:
        print("No state file found")

    # Show recent logs
    print()
    print("=== Recent Log ===")
    if os.path.isfile(LOG_FILE):
        with open(LOG_FILE) as f:
            lines = f.readlines()
            for line in lines[-10:]:
                print(line, end="")
    else:
        print("No log file found")


def _tail_log():
    if not os.path.isfile(LOG_FILE):
        print(f"Error: {LOG_FILE} not found", file=sys.stderr)
        sys.exit(1)
    os.execvp("tail", ["tail", "-n", "50", LOG_FILE])


def _stop_program():
    if not os.path.isfile(PID_FILE):
        print("No running auto program found")
        return

    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        print("Error: PID file corrupted. Removing it.", file=sys.stderr)
        os.remove(PID_FILE)
        return

    print(f"Stopping auto program (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly
        for _ in range(10):
            time.sleep(1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        print("Program stopped")
    except ProcessLookupError:
        print("Process already stopped")

    os.remove(PID_FILE)


if __name__ == "__main__":
    main()
