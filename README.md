# loom

A single primitive for self-controlling agents: `step()`.

The model writes Python. Python calls `step()`. Each `step()` is a full agent turn with tool access. Python provides the control flow. Every agent architecture pattern (ReAct, RLM, task trees, self-guardrailing) emerges as a special case.

## Install

```bash
pip install loom-agent
```

Requires Python 3.10+ and a running [OpenCode](https://github.com/nicholasgriffintn/opencode) server (`opencode serve`).

## Quick Start

```python
import asyncio
from loom import step

async def main():
    result = await step("What files are in the current directory?")
    print(result)

asyncio.run(main())
```

## Structured Output

Pass a `schema` to get structured results your code can branch on:

```python
result = await step(
    "Run train.py and report the validation loss",
    schema={"val_bpb": "float"}
)
if result["val_bpb"] < best:
    print("Improved!")
```

## Context

Pass state between steps explicitly — each step gets a fresh context window:

```python
result = await step(
    "Propose one experiment",
    context={"best_so_far": 3.2, "experiment_number": 5},
    schema={"description": "str", "val_bpb": "float"}
)
```

## Example: Autoresearch

See [`examples/autoresearch.py`](examples/autoresearch.py) — an autonomous research loop that proposes experiments, runs them, keeps improvements, and periodically replans.

## How It Works

- Each `step()` creates a fresh OpenCode session (clean context window)
- The model inside a step has full tool access (bash, file edit, etc.)
- Cross-step state flows through Python variables, not context accumulation
- Step 1000 is as lucid as step 1

## Docs

- [Quickstart](docs/quickstart.md) — get running in 5 minutes
- [API Reference](docs/api.md) — full `step()` and `StepRuntime` docs
- [Design](docs/design.md) — why `step()` works this way

## License

MIT
