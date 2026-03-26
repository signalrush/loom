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

        # Agent registry
        self._agents: dict[str, dict] = {}

        # Step counter
        self._step_count = 0

    def agent(self, name: str, cwd: str = None) -> None:
        """Declare an agent. First declaration wins."""
        if name in self._agents:
            return
        self._agents[name] = {
            "cwd": cwd or self._cwd,
            "session_id": None,
        }

    async def remind(self, instruction: str, schema: dict = None,
                     timeout: int = None) -> str | dict:
        """Send yourself a message via stop-hook IPC."""
        self._step_count += 1
        step_num = self._step_count
        _log(f"Step {step_num}: {instruction[:80]}...")

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

        _log(f"Step {step_num}: waiting for response...")
        if timeout is not None:
            response_text = await asyncio.wait_for(
                self._wait_for_response(step_num), timeout=timeout)
        else:
            response_text = await self._wait_for_response(step_num)

        _log(f"Step {step_num}: got response ({len(response_text or '')}b)")

        if schema is None:
            return response_text

        return await self._parse_with_retries(response_text, schema, step_num)

    async def task(self, instruction: str, to: str, schema: dict = None,
                   timeout: int = None) -> str | dict:
        """Assign work to another agent via claude -p subprocess."""
        if to not in self._agents:
            self.agent(to)

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

        try:
            return _extract_json(response_text)
        except ValueError:
            raise ValueError(
                f"Agent '{to}' response was not valid JSON: "
                f"{response_text[:200]}"
            )

    def cleanup(self) -> None:
        """Terminate all agent sessions. Called on program exit."""
        for name, config in self._agents.items():
            handle = config.get("_handle")
            if handle:
                _log(f"Cleaning up agent '{name}'")
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

            if cur_status != last_status:
                elapsed = time.monotonic() - start
                _log(f"_wait({step_number}): status={cur_status}, step={cur_step} [{elapsed:.1f}s]")
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
                    response_text = await self._wait_for_response(self._step_count)
                else:
                    raise ValueError(
                        f"JSON parse failed after 3 attempts: {response_text[:200]}"
                    )
