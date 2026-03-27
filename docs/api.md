# API Reference

## `async def main(auto)`

The main program pattern. Write a function that takes `auto` as its argument. The `auto` object provides `remind()`, `task()`, and `agent()` for orchestrating work.

```python
async def main(auto):
    result = await auto.remind("What files are in the current directory?")
    print(result)
```

## `auto.remind(instruction, schema=None, timeout=None)`

Send yourself a message. Your session executes the instruction and returns the result. Each call is one full agent turn in a persistent session where context accumulates.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instruction` | `str` | required | What to do. Natural language. |
| `schema` | `dict \| None` | `None` | If provided, forces structured JSON output. Keys are field names, values are type descriptions. |
| `timeout` | `int \| None` | `None` | Seconds. Raises `TimeoutError` if exceeded. |

### Returns

- **Without schema:** `str` — the model's text response.
- **With schema:** `dict` — parsed JSON matching the schema.

### Examples

```python
async def main(auto):
    # Simple instruction
    result = await auto.remind("What files are in the current directory?")

    # With schema
    result = await auto.remind(
        "Run the training script and report results.",
        schema={"loss": "float", "accuracy": "float", "epochs": "int"}
    )
    # result = {"loss": 0.23, "accuracy": 0.91, "epochs": 10}
```

---

## `auto.task(instruction, to, schema=None, timeout=None)`

Assign work to another agent. That agent's Claude Code session (via `claude -p`) executes the instruction and returns the result.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instruction` | `str` | required | What to do. Natural language. |
| `to` | `str` | required | Agent name. Must match a declared or implicitly created agent. |
| `schema` | `dict \| None` | `None` | Same as `remind`. |
| `timeout` | `int \| None` | `None` | Same as `remind`. |

### Returns

- **Without schema:** `str` — the agent's text response.
- **With schema:** `dict` — parsed JSON matching the schema.

### Examples

```python
async def main(auto):
    auto.agent("tester", cwd="/home/user/project")

    result = await auto.task("run all tests and report failures", to="tester")

    result = await auto.task(
        "benchmark the API endpoint",
        to="tester",
        schema={"rps": "float", "p99_ms": "float"}
    )
```

---

## `auto.agent(name, cwd=None)`

Declare an agent before first use. Optional — calling `task(to="name")` without prior declaration creates an agent with default config.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Unique agent identifier. |
| `cwd` | `str \| None` | `None` | Working directory for the agent's session. Defaults to program's cwd. |

---

## `run_program_v2(program_fn)`

Executes an auto program using the Auto orchestration object.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `program_fn` | `function` | required | Your main function that takes `auto` as argument. Must be async. |

### Example

```python
from auto.step import run_program_v2

async def main(auto):
    result = await auto.remind("Run tests and fix any failures.")
    print(result)

await run_program_v2(main)
```

---

## Key Behaviors

### Persistent session across steps

All `remind()` calls operate within a single session. Step 100 remembers everything from steps 1-99. Context accumulates naturally, while Python provides control flow and structured state management.

### Tool access

Inside a remind, the model has full access to bash, file read/write, and any configured MCP servers. `remind()` is a complete agent turn that accumulates context.

### Task isolation

Each `task()` runs in a separate `claude -p` subprocess. Agents don't share context with each other or with the main session. Use the return value to relay information between agents.

### Schema extraction

When `schema` is provided, the runtime:

1. Instructs the model to return JSON matching the schema
2. Extracts JSON from the response (handles markdown fences, surrounding text)
3. Parses and returns the structured object

If the model's response doesn't contain valid JSON, it retries up to 2 times before raising `ValueError`.
