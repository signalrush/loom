"""Basic tests for the loom.step module.

These are skeleton tests — the actual StepRuntime requires an OpenCode server,
so we test construction and interface only.
"""

import pytest


def test_step_runtime_import():
    """Verify loom.step can be imported."""
    from loom.step import StepRuntime
    assert StepRuntime is not None


def test_step_runtime_defaults():
    """StepRuntime initializes with sensible defaults."""
    from loom.step import StepRuntime
    rt = StepRuntime()
    assert rt.server_url == "http://localhost:54321"
    assert rt.cwd == "."


def test_step_runtime_custom_params():
    """StepRuntime accepts custom server_url and cwd."""
    from loom.step import StepRuntime
    rt = StepRuntime(server_url="http://example.com:9999", cwd="/tmp")
    assert rt.server_url == "http://example.com:9999"
    assert rt.cwd == "/tmp"


@pytest.mark.asyncio
async def test_step_requires_server():
    """Calling step without a running server should raise."""
    from loom.step import StepRuntime
    rt = StepRuntime(server_url="http://localhost:1")
    with pytest.raises(Exception):
        await rt.step("hello")
