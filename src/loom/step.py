import json
import re
from opencode_agent_sdk import SDKClient, AgentOptions, AssistantMessage, TextBlock


def _extract_json(text):
    """Extract JSON object from model response, handling markdown fences and surrounding text."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Find first { ... } or [ ... ] in the text
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        # Find matching closing bracket
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


class StepRuntime:
    def __init__(self, server_url="http://localhost:54321", cwd="."):
        self.server_url = server_url
        self.cwd = cwd

    async def step(self, instruction, context=None, schema=None):
        # Build prompt
        prompt = instruction
        if context is not None:
            prompt = f"Context:\n{context}\n\nTask: {instruction}"
        if schema is not None:
            # Build a clear description of expected output
            schema_desc = json.dumps(schema, indent=2)
            prompt += (
                f"\n\nRespond with ONLY a JSON object. The keys and their expected types are:\n"
                f"{schema_desc}\n\n"
                f"Replace the type descriptions with actual values. "
                f"For example, if the schema is {{\"name\": \"str\", \"age\": \"int\"}}, "
                f"you would return {{\"name\": \"Alice\", \"age\": 30}}.\n"
                f"Return ONLY the JSON object, no other text."
            )

        # Each step = fresh session (fresh context window)
        client = SDKClient(options=AgentOptions(
            server_url=self.server_url,
            cwd=self.cwd,
        ))
        await client.connect()
        await client.query(prompt)

        # Collect response — take only the last assistant message
        # (the first one may echo back the prompt)
        result = ""
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                text = ""
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                if text:
                    result = text  # overwrite, keeping only the last one

        await client.disconnect()

        # Parse schema if needed
        if schema is not None:
            return _extract_json(result)
        return result


# Default runtime instance
_default_runtime = None


def _get_default_runtime():
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = StepRuntime()
    return _default_runtime


async def step(instruction, context=None, schema=None):
    """The single primitive for self-controlling agents.

    Args:
        instruction: What to do. Natural language.
        context: What this step can see. Previous results, file contents, state.
        schema: If provided, the step must return structured output matching this schema.

    Returns:
        A string (default) or a structured object if schema was specified.
    """
    return await _get_default_runtime().step(instruction, context, schema)
