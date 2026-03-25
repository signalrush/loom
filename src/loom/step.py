"""Loom: the step() primitive.

The model writes a program with def main(step). loom-run executes it,
injecting step() which sends each instruction into the model's own session.

    def main(step):
        result = step("run train.py, report loss")
        step(f"loss was {result}, try to improve it")

step(instruction) -> str
step(instruction, schema={...}) -> dict
"""

import json
import re
import os
import asyncio
from opencode_agent_sdk import SDKClient, AgentOptions, AssistantMessage, TextBlock


def _extract_json(text):
    """Extract JSON object from model response, handling markdown fences and surrounding text."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not extract valid JSON from response: {text[:200]}")


async def _send_step(client, instruction, schema=None):
    """Send one step to the session and return the result."""
    prompt = instruction
    if schema is not None:
        schema_desc = json.dumps(schema, indent=2)
        prompt += (
            f"\n\nRespond with ONLY a JSON object. The keys and their expected types are:\n"
            f"{schema_desc}\n\n"
            f"Replace the type descriptions with actual values. "
            f"For example, if the schema is {{\"name\": \"str\", \"age\": \"int\"}}, "
            f"you would return {{\"name\": \"Alice\", \"age\": 30}}.\n"
            f"Return ONLY the JSON object, no other text."
        )

    await client.query(prompt)

    result = ""
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            text = ""
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text += block.text
            if text:
                result = text

    if schema is not None:
        return _extract_json(result)
    return result


async def run_program(program_fn, server_url=None, cwd=None):
    """Execute a loom program.

    The program_fn receives a step() function that sends instructions
    into a persistent session. Context accumulates across steps.

    Args:
        program_fn: A function that takes step as its argument.
                    Can be sync (def main(step)) or async (async def main(step)).
        server_url: OpenCode server URL. Defaults to LOOM_SERVER_URL env or localhost:54321.
        cwd: Working directory. Defaults to current directory.
    """
    server_url = server_url or os.environ.get("LOOM_SERVER_URL", "http://localhost:54321")
    cwd = cwd or os.getcwd()

    client = SDKClient(options=AgentOptions(
        server_url=server_url,
        cwd=cwd,
    ))
    await client.connect()

    try:
        async def step(instruction, schema=None):
            """Send an instruction to the model and get the result.

            Args:
                instruction: What to do. Natural language.
                schema: If provided, returns structured JSON output.

            Returns:
                str (default) or dict (if schema provided).
            """
            return await _send_step(client, instruction, schema)

        result = program_fn(step)
        # Support both sync and async main functions
        if asyncio.iscoroutine(result):
            await result
    finally:
        await client.disconnect()
