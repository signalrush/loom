import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from auto.agents import AgentHandle


def _mock_completed_process(result_text="done", session_id="test-uuid-123"):
    output = json.dumps({"result": result_text, "session_id": session_id})
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    mock.stderr = ""
    return mock


class TestAgentHandle:
    def test_first_call_stores_session_id(self, tmp_path):
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")
        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process("fixed it", "uuid-abc")):
            result = asyncio.run(agent.run("fix the bug"))
        assert result == "fixed it"
        assert agent.session_id == "uuid-abc"
        state = json.loads(state_path.read_text())
        assert state["session_id"] == "uuid-abc"

    def test_second_call_uses_resume(self, tmp_path):
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
        state_path = tmp_path / "coder.json"
        agent = AgentHandle("coder", cwd=str(tmp_path), state_path=state_path,
                           log_path=tmp_path / "coder.log")
        with patch("auto.agents.subprocess.run",
                   return_value=_mock_completed_process()) as mock_run:
            asyncio.run(agent.run("do something"))
        cmd = mock_run.call_args[0][0]
        assert "--resume" not in cmd

    def test_nonzero_exit_raises(self, tmp_path):
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
