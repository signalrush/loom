---
name: auto
description: Write Python programs that control your own execution with step(). Use when you need long-running loops, research, optimization, or any task requiring 10+ steps with branching logic. Triggers on "auto", "run a loop", "autoresearch", "step loop", "write a program", or when a program.py with def main(step) exists.
---

# Auto Skill — Self-Controlling Agent Programs

## What is Auto?

Auto lets you write a Python program that controls agent execution. You write `async def main(step)` — the `step` function sends instructions to an agent session. Context accumulates. The agent remembers everything.

The Python program is the control flow. `step()` is the agent acting.

## How to Use

### 1. Setup (once per project)

```bash
auto-run setup
```

### 2. Write a program

```python
# program.py

async def main(step):
    # Each step() is a turn in the agent's session — it remembers everything
    baseline = await step(
        "Run train.py and report val_loss",
        schema={"val_loss": "float"}
    )
    best = baseline["val_loss"]

    for i in range(20):
        result = await step(
            f"Experiment {i+1}: try to beat val_loss={best}. "
            "Edit train.py, commit, run, report.",
            schema={"val_loss": "float", "description": "str"}
        )

        if result["val_loss"] < best:
            best = result["val_loss"]
            await step(f"Good, improved to {best}. Keep it.")
        else:
            await step("Didn't improve. Revert: git reset --hard HEAD~1")

        if (i + 1) % 5 == 0:
            await step("Reflect: what's working? What to try next?")
```

That's it. No imports needed beyond the `step` function passed to `main`.

### 3. Run it

```bash
auto-run program.py
```

Then type **go** to start. Each step runs as a turn in your session — you can watch everything happen.

### 4. Monitor

```bash
auto-run status    # process status + state + recent logs
auto-run log       # tail live output
auto-run stop      # kill it
```

### 5. Steer

Kill, edit, restart:
```bash
auto-run stop
# edit program.py
auto-run program.py &
```

## step() API

```python
result = await step(instruction)              # returns str
result = await step(instruction, schema={})   # returns dict
```

- **instruction** (`str`): What to do. Natural language.
- **schema** (`dict`, optional): Forces structured JSON output. Keys are field names, values are type descriptions.

Each `step()` is a turn in the agent's session. The agent has full tool access (bash, file edit, etc) and remembers all previous steps.

## State tracking (optional)

For long-running programs, use `auto.state` to write progress to `auto-state.json`:

```python
from auto import state

async def main(step):
    state.set("status", "running")
    
    for i in range(100):
        result = await step(f"experiment {i}", schema={"score": "float"})
        state.update({"step": i, "score": result["score"]})
    
    state.set("status", "done")
```

Then `auto-run status` or `cat auto-state.json` shows progress.

## Patterns

### Simple loop
```python
async def main(step):
    for i in range(50):
        await step(f"Do task {i}")
```

### Loop with branching
```python
async def main(step):
    best = 999
    for i in range(20):
        r = await step(f"Try to beat {best}", schema={"loss": "float"})
        if r["loss"] < best:
            best = r["loss"]
        else:
            await step("Revert")
```

### Error handling
```python
async def main(step):
    for i in range(20):
        try:
            r = await step(f"Experiment {i}", schema={"loss": "float"})
        except Exception as e:
            await step(f"Failed: {e}. Try a simpler approach.")
```

### Replanning
```python
async def main(step):
    for i in range(100):
        await step(f"Experiment {i}")
        if (i + 1) % 10 == 0:
            await step("Reflect on last 10 experiments. Adjust strategy.")
```

## When to use Auto

**Use it for:** long-running loops, research, optimization, anything needing 10+ steps with branching logic

**Don't use it for:** one-shot tasks, simple questions — just do those in normal conversation

## Key insight

`step()` drives your session. The Python program controls when and how you work. Each step is a full turn with access to all tools.
