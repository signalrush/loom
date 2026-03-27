# auto

Three primitives for self-controlling agents: `remind()`, `task()`, and `agent()`.

You write a Python program. Each `remind()` is a turn in the model's own session — context accumulates, the model remembers everything. `task()` dispatches work to other agents. Python controls the flow. The model does the work.

## Install

```bash
npx skills add signalrush/auto
```

Works with Claude Code, Codex, Cursor, OpenCode, Windsurf, and 40+ other agents.

### Manual setup

```bash
pip install auto-agent
```

Requires Python 3.10+ and [OpenCode](https://github.com/opencode-ai/opencode) (`opencode serve`).

## Quick Start

Write a program:

```python
# program.py

async def main(auto):
    result = await auto.remind("What files are in the current directory?")
    print(result)
```

Run it:

```bash
auto-run program.py
```

## How it works

```
opencode serve --port 54321
       │
       ├── TUI (opencode attach) — you watch
       │
       └── auto-run program.py — feeds steps into the same session
```

Each `remind()` is a turn in the model's own session. The model remembers all previous steps. Python handles loops, branching, and state.

## Structured Output

Use `schema` when Python needs to make decisions:

```python
async def main(auto):
    result = await auto.remind(
        "Run train.py and report the validation loss",
        schema={"val_loss": "float"}
    )
    if result["val_loss"] < 0.5:
        await auto.remind("Good enough. Stop experimenting.")
    else:
        await auto.remind("Try a higher learning rate.")
```

## Example: Autoresearch

```python
async def main(auto):
    baseline = await auto.remind("Run train.py, report val_loss", schema={"val_loss": "float"})
    best = baseline["val_loss"]

    for i in range(20):
        result = await auto.remind(
            f"Experiment {i+1}: beat val_loss={best}. Edit, commit, run, report.",
            schema={"val_loss": "float", "description": "str"}
        )
        if result["val_loss"] < best:
            best = result["val_loss"]
        else:
            await auto.remind("Revert: git reset --hard HEAD~1")

        if (i + 1) % 5 == 0:
            await auto.remind("Reflect: what's working? Adjust strategy.")
```

See [`examples/autoresearch.py`](examples/autoresearch.py) for the full version.

## Multi-Agent

```python
async def main(auto):
    auto.agent("researcher", cwd="/home/user/papers")
    auto.agent("coder", cwd="/home/user/project")

    findings = await auto.task("Survey recent papers on topic X", to="researcher")
    await auto.task(f"Implement based on: {findings}", to="coder")
```

## Monitor & Steer

```bash
auto-run status    # process status + state
auto-run log       # tail live output
auto-run stop      # kill it
```

Steer by killing, editing `program.py`, and restarting. State persists in `auto-state.json`.

## Key Insight

`remind()` is the model continuing to work in its own session. `task()` dispatches to other agents. The Python program is just control flow around the model's actions. Every agent architecture pattern (ReAct, experiment loops, task trees) is a special case of these primitives + Python.

## Docs

- [Quickstart](docs/quickstart.md)
- [API Reference](docs/api.md)
- [Design](docs/design.md)

## License

MIT
