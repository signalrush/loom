"""Integration tests against a live OpenCode server.

Requires:
  - ANTHROPIC_API_KEY set in environment
  - OpenCode server running on localhost:54321
"""

import os
import pytest
from loom.step import run_program

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]

SERVER_URL = "http://localhost:54321"


async def test_simple_step():
    """Basic step with a simple question."""
    results = []

    async def program(step):
        result = await step("What is 2 + 2? Reply with just the number.")
        results.append(result)

    await run_program(program, server_url=SERVER_URL, cwd="/tmp")
    assert "4" in results[0]


async def test_persistent_session():
    """Steps share the same session — model remembers previous steps."""
    results = []

    async def program(step):
        await step("Remember: the secret word is 'banana'.")
        result = await step("What is the secret word I just told you?")
        results.append(result)

    await run_program(program, server_url=SERVER_URL, cwd="/tmp")
    assert "banana" in results[0].lower()


async def test_step_with_schema():
    """Step that returns structured JSON."""
    results = []

    async def program(step):
        result = await step(
            "What is 2 + 2?",
            schema={"answer": "int", "explanation": "str"},
        )
        results.append(result)

    await run_program(program, server_url=SERVER_URL, cwd="/tmp")
    assert isinstance(results[0], dict)
    assert results[0]["answer"] == 4


async def test_multi_step_with_schema():
    """Multiple steps with schema, context accumulates."""
    results = []

    async def program(step):
        r1 = await step("Pick a random number between 1 and 10.", schema={"number": "int"})
        results.append(r1)
        r2 = await step(
            "Double the number you just picked.",
            schema={"original": "int", "doubled": "int"}
        )
        results.append(r2)

    await run_program(program, server_url=SERVER_URL, cwd="/tmp")
    assert isinstance(results[0], dict)
    assert isinstance(results[1], dict)
    # The model should remember and double the same number
    assert results[1]["doubled"] == results[1]["original"] * 2
