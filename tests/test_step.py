"""Unit tests for auto.step module."""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from auto.step import _extract_json, _write_state, _read_state, _state_file_path


# --- _extract_json tests ---

def test_extract_json_direct():
    assert _extract_json('{"answer": 42}') == {"answer": 42}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"answer": 42}\n```') == {"answer": 42}


def test_extract_json_fenced_no_lang():
    assert _extract_json('```\n{"answer": 42}\n```') == {"answer": 42}


def test_extract_json_surrounded():
    assert _extract_json('Here is the result: {"answer": 42} Hope that helps!') == {"answer": 42}


def test_extract_json_last_object():
    """When multiple JSON objects exist, returns the LAST one (rfind behavior)."""
    result = _extract_json('text {"a": 1} more text {"b": 2}')
    assert result == {"b": 2}


def test_extract_json_array():
    assert _extract_json('[1, 2, 3]') == [1, 2, 3]


def test_extract_json_nested():
    text = 'Result: {"outer": {"inner": [1, 2]}}'
    assert _extract_json(text) == {"outer": {"inner": [1, 2]}}


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        _extract_json("no json here at all")


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        _extract_json("")


# --- _write_state / _read_state tests ---

def test_write_read_roundtrip(tmp_path):
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        data = {"status": "pending", "step_number": 1, "instruction": "test"}
        _write_state(data)
        result = _read_state()
        assert result["status"] == "pending"
        assert result["step_number"] == 1
        assert result["instruction"] == "test"
        assert "updated_at" in result  # auto-injected


def test_write_state_creates_directory(tmp_path):
    state_file = tmp_path / "new" / "deep" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        _write_state({"status": "test"})
        assert state_file.exists()


def test_read_state_missing_returns_none(tmp_path):
    state_file = tmp_path / "nonexistent" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        assert _read_state() is None


def test_write_state_atomic(tmp_path):
    """No partial writes — temp file is used."""
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        _write_state({"status": "first"})
        # Write again — should not corrupt even if read during write
        _write_state({"status": "second"})
        result = _read_state()
        assert result["status"] == "second"


# --- _wait_for_response tests ---

@pytest.mark.asyncio
async def test_wait_for_response_normal(tmp_path):
    from auto.step import _wait_for_response
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        with patch("auto.step.POLL_INTERVAL", 0.01):
            # Write responded state
            _write_state({"status": "responded", "step_number": 1, "response": "hello"})
            result = await _wait_for_response(1)
            assert result == "hello"


@pytest.mark.asyncio
async def test_wait_for_response_wrong_step_then_disappears(tmp_path):
    """When the step number doesn't match, polling continues until state disappears."""
    from auto.step import _wait_for_response
    import asyncio
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        with patch("auto.step.POLL_INTERVAL", 0.01):
            # Write responded but for wrong step
            _write_state({"status": "responded", "step_number": 99, "response": "wrong"})

            async def delete_after_delay():
                await asyncio.sleep(0.05)
                state_file.unlink()

            asyncio.create_task(delete_after_delay())
            with pytest.raises(RuntimeError, match="disappeared"):
                await _wait_for_response(1)


@pytest.mark.asyncio
async def test_wait_for_response_error_status(tmp_path):
    from auto.step import _wait_for_response
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        with patch("auto.step.POLL_INTERVAL", 0.01):
            _write_state({"status": "error", "step_number": 1, "error": "hook died"})
            with pytest.raises(RuntimeError, match="hook died"):
                await _wait_for_response(1)


@pytest.mark.asyncio
async def test_wait_for_response_stale_error_ignored(tmp_path):
    """B21: stale error from a previous step (lower step_number) is ignored."""
    from auto.step import _wait_for_response
    import asyncio
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        with patch("auto.step.POLL_INTERVAL", 0.01):
            # error from step 1 should not kill a wait for step 2
            _write_state({"status": "error", "step_number": 1, "error": "old error"})

            async def resolve_after_delay():
                await asyncio.sleep(0.05)
                _write_state({"status": "responded", "step_number": 2, "response": "ok"})

            asyncio.create_task(resolve_after_delay())
            result = await _wait_for_response(2)
            assert result == "ok"


@pytest.mark.asyncio
async def test_wait_for_response_file_disappears(tmp_path):
    from auto.step import _wait_for_response
    state_file = tmp_path / ".claude" / "auto-loop.json"
    with patch("auto.step._state_file_path", return_value=state_file):
        with patch("auto.step.POLL_INTERVAL", 0.01):
            # No file at all
            with pytest.raises(RuntimeError, match="disappeared"):
                await _wait_for_response(1)
