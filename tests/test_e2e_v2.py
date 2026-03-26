"""End-to-end tests for the v2 Auto API.

Tests the full flow without a live Claude session by simulating
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
        with open(state_path) as f:
            state = json.load(f)
        state["status"] = "responded"
        state["response"] = response
        with open(state_path, "w") as f:
            json.dump(state, f)
    Thread(target=_write, daemon=True).start()


class TestRemindE2E:
    def test_remind_full_cycle(self, tmp_path):
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

        async def run():
            _simulate_hook_response(auto._self_state_path, 1, "the answer is 42")
            result = await auto.remind("what is the answer?")
            assert result == "the answer is 42"

        asyncio.run(run())

    def test_remind_with_schema_e2e(self, tmp_path):
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

        async def run():
            _simulate_hook_response(auto._self_state_path, 1, '{"score": 0.95}')
            result = await auto.remind("rate it", schema={"score": "float"})
            assert result["score"] == 0.95

        asyncio.run(run())

    def test_two_reminds_sequential(self, tmp_path):
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

        async def run():
            _simulate_hook_response(auto._self_state_path, 1, "first")
            r1 = await auto.remind("step 1")
            assert r1 == "first"

            _simulate_hook_response(auto._self_state_path, 2, "second")
            r2 = await auto.remind("step 2")
            assert r2 == "second"

        asyncio.run(run())

    def test_remind_timeout(self, tmp_path):
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

        async def run():
            try:
                await auto.remind("do something", timeout=0.5)
                assert False, "Should have raised TimeoutError"
            except (TimeoutError, asyncio.TimeoutError):
                pass

        asyncio.run(run())
