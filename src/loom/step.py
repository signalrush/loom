import json
from opencode_agent_sdk import SDKClient, AgentOptions, AssistantMessage, TextBlock


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
            prompt += f"\n\nYou must return valid JSON matching this schema: {json.dumps(schema)}"

        # Each step = fresh session (fresh context window)
        client = SDKClient(options=AgentOptions(
            server_url=self.server_url,
            cwd=self.cwd,
        ))
        await client.connect()
        await client.query(prompt)

        # Collect response
        result = ""
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result += block.text

        await client.disconnect()

        # Parse schema if needed
        if schema is not None:
            return json.loads(result)
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
