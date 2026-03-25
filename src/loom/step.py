"""Loom: the step() primitive.

The model writes a program with def main(step). loom-run executes it,
injecting step() which sends each instruction into an agent session
via the opencode serve HTTP API.

    async def main(step):
        result = await step("run train.py, report loss")
        await step(f"loss was {result}, try to improve it")

step(instruction) -> str
step(instruction, schema={...}) -> dict
"""

import json
import re
import os
import httpx


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


async def _send_step(client, server_url, session_id, instruction, schema=None):
    """Send one step to the session via REST API and return the result."""
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

    resp = await client.post(
        f"{server_url}/session/{session_id}/message",
        json={"parts": [{"type": "text", "text": prompt}]},
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract text from response parts
    result = ""
    parts = data.get("parts", [])
    for part in parts:
        if part.get("type") == "text":
            result = part["text"]  # take the last text part

    if schema is not None:
        return _extract_json(result)
    return result


async def _get_or_create_session(client, server_url, session_id=None):
    """Get an existing session or create a new one.

    If session_id is provided, uses that directly.
    Otherwise lists sessions and uses the most recent one,
    or creates a new session if none exist.
    """
    if session_id:
        return session_id

    # List existing sessions and use the most recent
    resp = await client.get(f"{server_url}/session")
    resp.raise_for_status()
    sessions = resp.json()

    if sessions:
        # Sort by updated time, use most recent
        sessions.sort(key=lambda s: s.get("time", {}).get("updated", 0), reverse=True)
        return sessions[0]["id"]

    # No sessions exist, create one
    resp = await client.post(f"{server_url}/session")
    resp.raise_for_status()
    return resp.json()["id"]


async def run_program(program_fn, server_url=None, cwd=None, session_id=None):
    """Execute a loom program.

    Sends steps to an opencode serve instance via HTTP. All steps run in
    the SAME session — the agent remembers everything.

    By default, attaches to the most recent existing session (the one
    visible in the TUI). Set LOOM_SESSION_ID to target a specific session.

    Requires `opencode serve` to be running.

    Args:
        program_fn: An async function that takes step as its argument.
        server_url: OpenCode server URL. Defaults to LOOM_SERVER_URL env or localhost:54321.
        cwd: Working directory (unused currently, reserved for future).
        session_id: Session ID to use. If not set, checks LOOM_SESSION_ID env.
                    If neither is set, uses the most recent session.
    """
    server_url = (server_url or os.environ.get("LOOM_SERVER_URL", "http://localhost:54321")).rstrip("/")
    session_id = session_id or os.environ.get("LOOM_SESSION_ID")

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        session_id = await _get_or_create_session(client, server_url, session_id)
        print(f"[loom] Using session: {session_id}")
        print(f"[loom] Server: {server_url}")

        step_count = 0

        async def step(instruction, schema=None):
            """Send an instruction to the agent and get the result.

            Args:
                instruction: What to do. Natural language.
                schema: If provided, returns structured JSON output.

            Returns:
                str (default) or dict (if schema provided).
            """
            nonlocal step_count
            step_count += 1
            print(f"[loom] Step {step_count}: {instruction[:80]}...")
            result = await _send_step(client, server_url, session_id, instruction, schema)
            result_preview = json.dumps(result)[:100] if isinstance(result, dict) else result[:100]
            print(f"[loom] Step {step_count} result: {result_preview}")
            return result

        await program_fn(step)
        print(f"[loom] Program complete ({step_count} steps)")
