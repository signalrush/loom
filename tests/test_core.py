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
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        assert auto._self_state_path == auto.run_dir / "self.json"


class TestAutoAgent:
    def test_agent_declaration(self, tmp_path):
        """auto.agent() stores config."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        auto.agent("coder", cwd="/app")
        assert "coder" in auto._agents
        assert auto._agents["coder"]["cwd"] == "/app"

    def test_agent_redeclaration_is_noop(self, tmp_path):
        """Second auto.agent() call with same name is ignored."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        auto.agent("coder", cwd="/app")
        auto.agent("coder", cwd="/other")
        assert auto._agents["coder"]["cwd"] == "/app"

    def test_agent_default_cwd(self, tmp_path):
        """Agent without cwd= uses project root."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        auto.agent("helper")
        assert auto._agents["helper"]["cwd"] == str(tmp_path)


class TestAutoRemind:
    def test_remind_writes_pending_state(self, tmp_path):
        """remind() writes pending status to self.json."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

        async def mock_wait(step_num):
            return "test response"

        with patch.object(auto, "_wait_for_response", side_effect=mock_wait):
            result = asyncio.run(auto.remind("do something"))

        assert result == "test response"
        state = json.loads((auto.run_dir / "self.json").read_text())
        assert state["step_number"] == 1

    def test_remind_with_schema_returns_dict(self, tmp_path):
        """remind() with schema parses JSON response."""
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")

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
        auto = Auto(project_root=tmp_path, auto_dir=tmp_path / ".auto")
        call_count = 0

        async def mock_wait(step_num):
            nonlocal call_count
            call_count += 1
            return f"response {call_count}"

        with patch.object(auto, "_wait_for_response", side_effect=mock_wait):
            asyncio.run(auto.remind("first"))
            asyncio.run(auto.remind("second"))

        assert auto._step_count == 2
