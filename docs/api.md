# API Reference

## `step(instruction, context=None, schema=None)`

The single primitive. Each call is one full agent turn — fresh context window, full tool access.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instruction` | `str` | required | What to do. Natural language. |
| `context` | `str \| dict \| None` | `None` | What this step can see. Previous results, state, file contents. Passed as a string prefix to the prompt. |
| `schema` | `dict \| None` | `None` | If provided, the step must return structured output matching this schema. Keys are field names, values are type descriptions. |

### Returns

- **Without schema:** `str` — the model's text response.
- **With schema:** `dict` — parsed JSON matching the schema.

### Examples

```python
from loom import step

# Simple instruction
result = await step("What files are in the current directory?")

# With context
result = await step(
    "What experiment should I try next?",
    context={"best_score": 0.85, "tried": ["lr=0.01", "lr=0.001"]}
)

# With schema
result = await step(
    "Run the training script and report results.",
    schema={"loss": "float", "accuracy": "float", "epochs": "int"}
)
# result = {"loss": 0.23, "accuracy": 0.91, "epochs": 10}
```

---

## `StepRuntime`

Manages the connection to an OpenCode server. The module-level `step()` function uses a default runtime instance.

### Constructor

```python
StepRuntime(server_url="http://localhost:54321", cwd=".")
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_url` | `str` | `"http://localhost:54321"` | OpenCode server URL. |
| `cwd` | `str` | `"."` | Working directory for agent tool execution. |

### Methods

#### `async step(instruction, context=None, schema=None)`

Same as the module-level `step()`. See above.

### Example

```python
from loom import StepRuntime

rt = StepRuntime(server_url="http://localhost:54321", cwd="/path/to/project")

result = await rt.step("Run tests and fix any failures.")
```

---

## Key Behaviors

### Fresh session per step

Each `step()` call creates a new OpenCode session. Step 100 has the same clean context as step 1. Cross-step state is passed explicitly through `context`, not accumulated in a context window.

### Tool access

Inside a step, the model has full access to bash, file read/write, and any configured MCP servers. The caller doesn't manage tools — `step()` is a complete agent turn.

### Schema extraction

When `schema` is provided, the runtime:

1. Instructs the model to return JSON matching the schema
2. Extracts JSON from the response (handles markdown fences, surrounding text)
3. Parses and returns the structured object

If the model's response doesn't contain valid JSON, a `ValueError` is raised.

### Prompt echo handling

The OpenCode SDK may echo the prompt as the first `AssistantMessage`. The runtime automatically discards this and uses only the model's actual response.
