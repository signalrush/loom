import json
import re
import os
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
    """Runtime that manages a persistent session with the OpenCode server.

    Each step() call is a new query in the SAME session — context accumulates
    across steps. The model remembers previous steps, tool results, and state.
    The Python program controls flow (loops, branching) while the model retains
    full conversational memory.
    """

    def __init__(self, server_url=None, cwd="."):
        self.server_url = server_url or os.environ.get("LOOM_SERVER_URL", "http://localhost:54321")
        self.cwd = cwd
        self._client = None

    async def _ensure_connected(self):
        """Connect to the server if not already connected."""
        if self._client is None:
            self._client = SDKClient(options=AgentOptions(
                server_url=self.server_url,
                cwd=self.cwd,
            ))
            await self._client.connect()

    async def step(self, instruction, context=None, schema=None):
        """Execute one step in the persistent session.

        Args:
            instruction: What to do. Natural language.
            context: Additional context to include with the instruction.
                     This is appended to the prompt, NOT a replacement for
                     the model's accumulated memory.
            schema: If provided, the step must return structured JSON output.

        Returns:
            A string (default) or a parsed dict if schema was specified.
        """
        # Build prompt
        prompt = instruction
        if context is not None:
            prompt = f"Context:\n{context}\n\nTask: {instruction}"
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

        # Reuse the same session — context accumulates
        await self._ensure_connected()
        await self._client.query(prompt)

        # Collect response — take only the last assistant message
        # (the first one may echo back the prompt)
        result = ""
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                text = ""
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                if text:
                    result = text

        # Parse schema if needed
        if schema is not None:
            return _extract_json(result)
        return result

    async def close(self):
        """Disconnect from the server."""
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, *args):
        await self.close()


# Default runtime instance
_default_runtime = None


def _get_default_runtime():
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = StepRuntime()
    return _default_runtime


async def step(instruction, context=None, schema=None):
    """The single primitive for self-controlling agents.

    Each call is a new turn in the same persistent session. The model
    remembers all previous steps — context accumulates naturally.

    Args:
        instruction: What to do. Natural language.
        context: Additional context for this step (appended to prompt).
        schema: If provided, the step must return structured output matching this schema.

    Returns:
        A string (default) or a structured object if schema was specified.
    """
    return await _get_default_runtime().step(instruction, context, schema)
