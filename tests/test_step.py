"""Unit tests for loom.step module."""

import pytest


def test_step_module_import():
    """Verify loom.step can be imported."""
    from loom.step import run_program, _extract_json, _send_step


def test_run_program_import():
    """Verify run_program is importable from loom."""
    from loom import run_program


def test_extract_json_direct():
    """Test JSON extraction from clean input."""
    from loom.step import _extract_json

    result = _extract_json('{"answer": 42}')
    assert result == {"answer": 42}


def test_extract_json_fenced():
    """Test JSON extraction from markdown fences."""
    from loom.step import _extract_json

    result = _extract_json('```json\n{"answer": 42}\n```')
    assert result == {"answer": 42}


def test_extract_json_surrounded():
    """Test JSON extraction from text with surrounding content."""
    from loom.step import _extract_json

    result = _extract_json('Here is the result: {"answer": 42} Hope that helps!')
    assert result == {"answer": 42}


def test_extract_json_invalid():
    """Test that invalid JSON raises ValueError."""
    from loom.step import _extract_json

    with pytest.raises(ValueError):
        _extract_json("no json here at all")


def test_env_var_default():
    """Test that LOOM_SERVER_URL env var is checked."""
    import os
    from loom.step import run_program
    # run_program reads LOOM_SERVER_URL — just verify it's referenced in the module
    import loom.step
    assert "LOOM_SERVER_URL" in open(loom.step.__file__).read()
