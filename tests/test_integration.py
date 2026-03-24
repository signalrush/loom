"""Integration tests against a live OpenCode server.

Requires:
  - ANTHROPIC_API_KEY set in environment
  - OpenCode server running on localhost:54321
"""

import asyncio
import os
import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


@pytest.fixture
def runtime():
    from loom.step import StepRuntime
    return StepRuntime(server_url="http://localhost:54321", cwd="/tmp")


async def test_simple_step(runtime):
    """Basic step with a simple question."""
    result = await runtime.step("What is 2 + 2? Reply with just the number.")
    assert "4" in result


async def test_step_with_context(runtime):
    """Step that uses context."""
    result = await runtime.step(
        "What is the animal?",
        context="The animal is a cat.",
    )
    assert "cat" in result.lower()


async def test_step_with_schema(runtime):
    """Step that returns structured JSON."""
    result = await runtime.step(
        "What is 2 + 2?",
        schema={"answer": "int", "explanation": "str"},
    )
    assert isinstance(result, dict)
    assert "answer" in result
    assert result["answer"] == 4


async def test_step_with_tool_use(runtime):
    """Step that requires tool use (bash)."""
    result = await runtime.step(
        "Run `echo hello_loom` in bash and report what it printed. Just say the output.",
    )
    assert "hello_loom" in result


async def test_fresh_session_per_step(runtime):
    """Each step should be a fresh session — no memory of previous steps."""
    await runtime.step("Remember: the secret word is 'banana'.")
    result = await runtime.step(
        "What is the secret word I told you? If you don't know, say 'unknown'.",
    )
    assert "unknown" in result.lower() or "don't" in result.lower() or "no" in result.lower()


async def test_step_schema_complex(runtime):
    """Step with a more complex schema."""
    result = await runtime.step(
        "List 3 primary colors.",
        schema={
            "colors": ["str"],
            "count": "int",
        },
    )
    assert isinstance(result, dict)
    assert "colors" in result
    assert isinstance(result["colors"], list)
    assert len(result["colors"]) == 3
