# Quickstart

## Prerequisites

- Python 3.10+
- An OpenCode server (or the `opencode` CLI installed locally)
- An API key for your LLM provider (e.g., `ANTHROPIC_API_KEY`)

## Install

```bash
npx skills add signalrush/loom
```

Or manually:

```bash
pip install loom-agent
```

## Start OpenCode

```bash
# Install opencode
npm install -g opencode-ai

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Start the server
opencode serve --port 54321
```

## Write a program

```python
# program.py
async def main(step):
    # A single step — one agent turn
    result = await step("What is 2 + 2? Just the number.")
    print(result)  # "4"

    # A step with tool use
    result = await step("List all Python files in the current directory.")
    print(result)

    # A step with structured output
    result = await step(
        "Count the lines in each Python file.",
        schema={"files": [{"name": "str", "lines": "int"}]}
    )
    print(result)  # {"files": [{"name": "main.py", "lines": 42}, ...]}
```

## Run it

```bash
loom-run program.py
```

## Write a loop

The power of `step()` is that the model controls the flow, but Python controls the state:

```python
# autoresearch.py
async def main(step):
    # Get baseline
    baseline = await step(
        "Run `python train.py` and report the validation loss.",
        schema={"val_loss": "float"}
    )
    best = baseline["val_loss"]

    for i in range(20):
        result = await step(
            "Propose and run one experiment. Edit train.py, run it, report results.",
            schema={"val_loss": "float", "description": "str"}
        )

        if result["val_loss"] < best:
            best = result["val_loss"]
            print(f"New best: {best} — {result['description']}")
        else:
            await step("Revert the last change. Git reset to previous commit.")
```

Run with `loom-run autoresearch.py`. The session persists across all steps — the model remembers every experiment. The Python loop handles state, branching, and control flow. The model handles the actual coding work inside each step.

## Next steps

- [API Reference](api.md) — full `step()` and `StepRuntime` docs
- [Design](design.md) — why `step()` works this way
- [Examples](../examples/) — complete example programs
