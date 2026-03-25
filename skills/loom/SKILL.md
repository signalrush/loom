# Loom Skill — Self-Controlling Agent Programs

## What is Loom?

Loom lets you write a Python program that controls your own execution. You write `async def main(step)` — the `step` function sends instructions back into your session. Context accumulates. You remember everything.

The Python program is your control flow. `step()` is you acting.

## How to Use

### 1. Write a program

```python
# program.py

async def main(step):
    # Each step() is a turn in your own session — you remember everything
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

### 2. Run it

```bash
loom-run program.py
```

### 3. Monitor

```bash
loom-run status    # process status + state + recent logs
loom-run log       # tail live output
loom-run stop      # kill it
```

### 4. Steer

Kill, edit, restart:
```bash
loom-run stop
# edit program.py
loom-run program.py
```

## step() API

```python
result = await step(instruction)              # returns str
result = await step(instruction, schema={})   # returns dict
```

- **instruction** (`str`): What to do. Natural language.
- **schema** (`dict`, optional): Forces structured JSON output. Keys are field names, values are type descriptions.

Each `step()` is a turn in your own session. You have full tool access (bash, file edit, etc). You remember all previous steps.

## State tracking (optional)

For long-running programs, use `loom.state` to write progress to `loom-state.json`:

```python
from loom import state

async def main(step):
    state.set("status", "running")
    
    for i in range(100):
        result = await step(f"experiment {i}", schema={"score": "float"})
        state.update({"step": i, "score": result["score"]})
    
    state.set("status", "done")
```

Then `loom-run status` or `cat loom-state.json` shows progress.

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

## When to use Loom

**Use it for:** long-running loops, research, optimization, anything needing 10+ steps with branching logic

**Don't use it for:** one-shot tasks, simple questions — just do those in normal conversation

## Key insight

`step()` is NOT a sub-agent. It's you, continuing to work. The Python program just controls when and how you work. You keep your full memory across all steps.
