"""Loom: the step() primitive.

The model writes a program with def main(step). loom-run executes it,
injecting step() which sends each instruction into the model's own session.

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


async def run_program(program_fn, server_url=None, cwd=None, session_id=None):
    """Execute a loom program.

    The program_fn receives a step() function that sends instructions
    into a persistent session. Context accumulates across steps.

    Args:
        program_fn: An async function that takes step as its argument.
        server_url: OpenCode server URL. Defaults to LOOM_SERVER_URL env or localhost:54321.
        cwd: Working directory (unused currently, reserved for future).
        session_id: Session ID to resume. If not set, checks LOOM_SESSION_ID env var.
                    If neither is set, creates a new session.
    """
    server_url = (server_url or os.environ.get("LOOM_SERVER_URL", "http://localhost:54321")).rstrip("/")
    session_id = session_id or os.environ.get("LOOM_SESSION_ID")

    # Workaround for OpenCode serve bug: the server doesn't properly append
    # user messages to session history, causing "assistant prefill" errors
    # on the second step. We create a fresh session per step and include
    # accumulated context in the prompt.
    history = []  # list of (instruction, response) tuples

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:

        async def step(instruction, schema=None):
            """Send an instruction to the model and get the result.

            Args:
                instruction: What to do. Natural language.
                schema: If provided, returns structured JSON output.

            Returns:
                str (default) or dict (if schema provided).
            """
            # Build context-enriched prompt from history
            if history:
                context_parts = ["[Previous steps in this session:]"]
                for i, (prev_instr, prev_resp) in enumerate(history, 1):
                    context_parts.append(f"\n--- Step {i} ---")
                    context_parts.append(f"Instruction: {prev_instr}")
                    context_parts.append(f"Response: {prev_resp}")
                context_parts.append(f"\n--- Current step (step {len(history) + 1}) ---")
                context_parts.append(instruction)
                full_instruction = "\n".join(context_parts)
            else:
                full_instruction = instruction

            # Create a fresh session for each step to avoid prefill bug
            resp = await client.post(f"{server_url}/session")
            resp.raise_for_status()
            step_session_id = resp.json()["id"]

            result = await _send_step(client, server_url, step_session_id, full_instruction, schema)

            # Store raw instruction and result for context
            result_str = json.dumps(result) if isinstance(result, dict) else result
            history.append((instruction, result_str))

            return result

        await program_fn(step)
