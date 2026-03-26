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

    def __init__(self, name: str, cwd: str, state_path: Path, log_path: Path):
        self.name = name
        self.cwd = cwd
        self.state_path = state_path
        self.log_path = log_path
        self.session_id: str | None = None
        self.step_count = 0

        existing = read_state(state_path)
        if existing and existing.get("session_id"):
            self.session_id = existing["session_id"]

    async def run(self, instruction: str, timeout: int = None) -> str:
        self.step_count += 1
        _log(f"[{self.name}] Step {self.step_count}: {instruction[:80]}...")

        write_state(self.state_path, {
            "name": self.name,
            "session_id": self.session_id,
            "status": "running",
            "step_number": self.step_count,
            "last_instruction": instruction,
            "cwd": self.cwd,
            "pid": os.getpid(),
        })

        cmd = ["claude", "-p", instruction, "--output-format", "json",
               "--dangerously-skip-permissions"]
        if self.session_id:
            cmd.extend(["--resume", self.session_id])

        _log(f"[{self.name}] Running: claude -p "
             f"{'--resume ' + self.session_id[:8] + '... ' if self.session_id else ''}"
             f"({len(instruction)}b instruction)")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                   cwd=self.cwd, timeout=timeout)
        except subprocess.TimeoutExpired:
            write_state(self.state_path, {
                "name": self.name, "session_id": self.session_id,
                "status": "error", "step_number": self.step_count,
                "last_instruction": instruction, "cwd": self.cwd,
                "pid": os.getpid(),
            })
            raise TimeoutError(f"Agent '{self.name}' timed out after {timeout}s")

        with open(self.log_path, "a") as f:
            f.write(f"--- Step {self.step_count} ---\n")
            if result.stdout:
                f.write(result.stdout + "\n")
            if result.stderr:
                f.write(f"STDERR: {result.stderr}\n")

        if result.returncode != 0:
            write_state(self.state_path, {
                "name": self.name, "session_id": self.session_id,
                "status": "error", "step_number": self.step_count,
                "last_instruction": instruction, "cwd": self.cwd,
                "pid": os.getpid(),
            })
            raise RuntimeError(
                f"Agent '{self.name}' failed (exit {result.returncode}): "
                f"{result.stderr[:500]}")

        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Agent '{self.name}' returned malformed JSON: {e}. "
                f"Output: {result.stdout[:200]}")

        response_text = output.get("result", "")
        new_session_id = output.get("session_id")
        if new_session_id:
            self.session_id = new_session_id

        _log(f"[{self.name}] Step {self.step_count}: done ({len(response_text)}b)")

        write_state(self.state_path, {
            "name": self.name, "session_id": self.session_id,
            "status": "idle", "step_number": self.step_count,
            "last_instruction": instruction, "cwd": self.cwd,
            "pid": os.getpid(),
        })

        return response_text
