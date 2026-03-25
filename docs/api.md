# API Reference

## `async def main(step)`

The main program pattern. Write a function that takes `step` as its argument. The `step` function sends instructions into your persistent session.

```python
async def main(step):
    result = await step("What files are in the current directory?")
    print(result)
```

## `step(instruction, schema=None)`

The single primitive. Each call is one full agent turn in a persistent session where context accumulates.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instruction` | `str` | required | What to do. Natural language. |
| `schema` | `dict \| None` | `None` | If provided, the step must return structured output matching this schema. Keys are field names, values are type descriptions. |

### Returns

- **Without schema:** `str` — the model's text response.
- **With schema:** `dict` — parsed JSON matching the schema.

### Examples

```python
async def main(step):
    # Simple instruction
    result = await step("What files are in the current directory?")

    # With schema
    result = await step(
        "Run the training script and report results.",
        schema={"loss": "float", "accuracy": "float", "epochs": "int"}
    )
    # result = {"loss": 0.23, "accuracy": 0.91, "epochs": 10}
```

---

## `run_program(program_fn, server_url=None, cwd=None)`

Executes a loom program by connecting to an OpenCode server and passing a `step` function to your main function.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `program_fn` | `function` | required | Your main function that takes `step` as argument. Can be sync or async. |
| `server_url` | `str` | `None` | OpenCode server URL. Defaults to `LOOM_SERVER_URL` env var or `http://localhost:54321`. |
| `cwd` | `str` | `None` | Working directory for agent tool execution. Defaults to current directory. |

### Example

```python
from loom import run_program

async def main(step):
    result = await step("Run tests and fix any failures.")
    print(result)

# Run the program
await run_program(main)
```

---

## Key Behaviors

### Persistent session across steps

All `step()` calls operate within a single OpenCode session. Step 100 remembers everything from steps 1-99. Context accumulates naturally, while Python provides control flow and structured state management.

### Tool access

Inside a step, the model has full access to bash, file read/write, and any configured MCP servers. The caller doesn't manage tools — `step()` is a complete agent turn that accumulates context.

### Schema extraction

When `schema` is provided, the runtime:

1. Instructs the model to return JSON matching the schema
2. Extracts JSON from the response (handles markdown fences, surrounding text)
3. Parses and returns the structured object

If the model's response doesn't contain valid JSON, a `ValueError` is raised.

### Prompt echo handling

The OpenCode SDK may echo the prompt as the first `AssistantMessage`. The runtime automatically discards this and uses only the model's actual response.
