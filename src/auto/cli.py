"""CLI entry point for auto-run."""

import json
import subprocess
import sys
import os
import signal
import time
from pathlib import Path

from auto.run_folder import create_run_folder

AUTO_DIR = os.path.join(str(Path.home()), ".auto")
PID_FILE = os.path.join(str(Path.home()), ".auto", "auto.pid")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("Usage: auto-run <command> [args...]")
        print()
        print("Commands:")
        print("    auto-run <program.py>   Start an auto program in background")
        print("    auto-run setup          Install stop hook into .claude/settings.local.json")
        print("    auto-run status         Show running state and recent logs")
        print("    auto-run log [agent]    Tail the latest log file (default: self)")
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
        agent_name = sys.argv[2] if len(sys.argv) > 2 else "self"
        _tail_log(agent_name)
    elif command == "stop":
        _stop_program()
    elif command.endswith(".py"):
        _start_program(command)
    else:
        print(f"Error: Unknown command '{command}'", file=sys.stderr)
        sys.exit(1)


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
        "timeout": 86400,  # 24h — hook blocks during task() execution
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
                return

    stop_hooks.append({"hooks": [hook_entry]})

    with open(settings_file, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[auto] Installed stop hook: {hook_script_abs}")
    print(f"[auto] Config written to: {settings_file.resolve()}")


def _start_program(program_path):
    """Start an auto program as a background process."""
    if not os.path.isfile(program_path):
        print(f"Error: {program_path} not found", file=sys.stderr)
        sys.exit(1)

    # Auto-setup hook + skill if not already installed
    _setup_hook()

    # Check if already running
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
        except (ValueError, OSError):
            Path(PID_FILE).unlink(missing_ok=True)
        else:
            try:
                os.kill(old_pid, 0)
                print(f"Error: Auto program already running (PID {old_pid})", file=sys.stderr)
                print("Use 'auto-run stop' first", file=sys.stderr)
                sys.exit(1)
            except (ProcessLookupError, PermissionError):
                Path(PID_FILE).unlink(missing_ok=True)

    program_path = os.path.abspath(program_path)
    cwd = os.getcwd()  # project root -- must match hook's working directory

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    # Create run folder under ~/.auto/ and pass path to child process
    auto_dir = Path(AUTO_DIR)
    run_dir = create_run_folder(auto_dir)
    run_log = str(run_dir / "logs" / "self.log")
    env["AUTO_RUN_DIR"] = str(run_dir)

    log_fh = open(run_log, "w")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", f"""
import asyncio, importlib.util, sys, os, inspect
os.chdir({cwd!r})
sys.path.insert(0, os.getcwd())
spec = importlib.util.spec_from_file_location('program', {program_path!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

params = list(inspect.signature(mod.main).parameters.keys())
if params and params[0] == 'auto':
    from auto.step import run_program_v2
    asyncio.run(run_program_v2(mod.main))
else:
    from auto.step import run_program
    asyncio.run(run_program(mod.main))
"""],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    except Exception:
        log_fh.close()
        raise
    log_fh.close()

    Path(PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    # Write PID -> run dir mapping so the hook can find the right run folder.
    # The hook has the session_id but not the run dir; the CLI has the run dir
    # but not the session_id. The PID file bridges them: hook finds the live PID,
    # reads the run dir, then registers sessions/<session_id> for future lookups.
    pids_dir = Path(AUTO_DIR) / "pids"
    pids_dir.mkdir(parents=True, exist_ok=True)
    with open(pids_dir / str(proc.pid), "w") as f:
        f.write(str(run_dir))

    print(f"[auto] Started in background (PID {proc.pid})")
    print(f"[auto] Run folder: {run_dir}")
    print(f"[auto] Logs: {run_log}")

    # Wait for Python to write any state (starting or pending) before printing
    # the "send go" message. run_program writes a "starting" heartbeat before
    # calling program_fn, so the file appears as soon as Python is alive even
    # if program_fn does slow initialization before its first step() call.
    state_file = run_dir / "self.json"
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
                Path(PID_FILE).unlink(missing_ok=True)
    else:
        print("Process: Not running")

    # Resolve the latest symlink to show the run folder name
    latest_path = Path(AUTO_DIR) / "latest"
    if latest_path.is_symlink():
        run_name = os.readlink(latest_path)
        print(f"Run: {run_name}")

    # Show agent states from all .json files in latest/
    print()
    print("=== Agents ===")
    if latest_path.exists():
        json_files = sorted(latest_path.glob("*.json"))
        if json_files:
            for jf in json_files:
                try:
                    with open(jf) as f:
                        state = json.load(f)
                    name = state.get("name", jf.stem)
                    status = state.get("status", "unknown")
                    step = state.get("step_number", "?")
                    instr = state.get("last_instruction", "")
                    print(f"  {name}: status={status} step={step} last_instruction={instr}")
                except (json.JSONDecodeError, OSError):
                    print(f"  {jf.stem}: (unreadable)")
        else:
            print("  No agent state files found")
    else:
        print("  No active run found")

    # Show recent logs
    print()
    print("=== Recent Log ===")
    log_path = latest_path / "logs" / "self.log"
    if log_path.is_file():
        print(f"(from {log_path.resolve()})")
        with open(log_path) as f:
            lines = f.readlines()
            for line in lines[-10:]:
                print(line, end="")
    else:
        print("No log file found")


def _tail_log(agent_name="self"):
    log_path = Path(AUTO_DIR) / "latest" / "logs" / f"{agent_name}.log"
    if log_path.is_file():
        target = str(log_path.resolve())
        print(f"(tailing {target})", file=sys.stderr)
        os.execvp("tail", ["tail", "-n", "50", str(log_path)])
    elif (Path(AUTO_DIR) / "latest").is_symlink():
        print(f"Error: Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Error: No active run found in {AUTO_DIR}", file=sys.stderr)
        sys.exit(1)


def _stop_program():
    if not os.path.isfile(PID_FILE):
        print("No running auto program found")
        return

    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        print("Error: PID file corrupted. Removing it.", file=sys.stderr)
        Path(PID_FILE).unlink(missing_ok=True)
        return

    print(f"Stopping auto program (PID {pid})...")
    try:
        os.killpg(pid, signal.SIGTERM)
        # Wait briefly
        for _ in range(10):
            time.sleep(1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.killpg(pid, signal.SIGKILL)
        print("Program stopped")
    except ProcessLookupError:
        print("Process already stopped")

    Path(PID_FILE).unlink(missing_ok=True)
    # Clean up PID -> run dir mapping
    (Path(AUTO_DIR) / "pids" / str(pid)).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
