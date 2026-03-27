# Quickstart

## Prerequisites

- Python 3.10+
- An OpenCode server (or the `opencode` CLI installed locally)
- An API key for your LLM provider (e.g., `ANTHROPIC_API_KEY`)

## Install

```bash
npx skills add signalrush/auto
```

Or manually:

```bash
pip install auto-agent
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
async def main(auto):
    # A single remind — one agent turn
    result = await auto.remind("What is 2 + 2? Just the number.")
    print(result)  # "4"

    # A remind with tool use
    result = await auto.remind("List all Python files in the current directory.")
    print(result)

    # A remind with structured output
    result = await auto.remind(
        "Count the lines in each Python file.",
        schema={"files": [{"name": "str", "lines": "int"}]}
    )
    print(result)  # {"files": [{"name": "main.py", "lines": 42}, ...]}
```

## Run it

```bash
auto-run program.py
```

## Write a loop

The power of `remind()` is that the model controls the work, but Python controls the state:

```python
# autoresearch.py
async def main(auto):
    # Get baseline
    baseline = await auto.remind(
        "Run `python train.py` and report the validation loss.",
        schema={"val_loss": "float"}
    )
    best = baseline["val_loss"]

    for i in range(20):
        result = await auto.remind(
            "Propose and run one experiment. Edit train.py, run it, report results.",
            schema={"val_loss": "float", "description": "str"}
        )

        if result["val_loss"] < best:
            best = result["val_loss"]
            print(f"New best: {best} — {result['description']}")
        else:
            await auto.remind("Revert the last change. Git reset to previous commit.")
```

Run with `auto-run autoresearch.py`. The session persists across all steps — the model remembers every experiment. The Python loop handles state, branching, and control flow. The model handles the actual coding work inside each remind.

## Multi-agent

```python
async def main(auto):
    auto.agent("tester", cwd="/home/user/project")

    await auto.remind("implement the new feature")
    test_result = await auto.task("run the full test suite and report", to="tester")
    await auto.remind(f"Fix issues from tests: {test_result}")
```

## Next steps

- [API Reference](api.md) — full `auto.remind()`, `auto.task()`, and `auto.agent()` docs
- [Design](design.md) — why auto works this way
- [Examples](../examples/) — complete example programs
