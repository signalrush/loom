---
name: auto
description: Run yourself in a loop with branching logic via a Python program. Use for long-running tasks like optimization, research, iterative improvement, or any multi-step workflow where you need to repeat, branch, or track progress across 10+ turns. Triggers on "auto", "run a loop", "autoresearch", "keep improving", or when a program.py with def main(auto) exists.
---

# Auto — Run yourself in a loop

A Python program drives your turns. Each `auto.remind()` becomes YOUR next turn — you execute it with full tool access. Use `auto.task()` to dispatch work to other agents. The program controls the loop, branching, and state.

## CRITICAL: How to launch

```bash
auto-run program.py
```

Then say **"go"** as your next message. That's it. The stop hook injects each step automatically after that.

**DO NOT:**
- Use `nohup`, `&`, or redirect output — `auto-run` handles backgrounding
- Run `auto-run log` (it blocks) — use `auto-run status` instead
- Stop the program because you see "Send any message to begin" — just say "go"
- Worry about CLAUDE_CODE_SESSION_ID — it works without it

## Writing a program

```python
# program.py
async def main(auto):
    # Each remind() is one of YOUR turns — you do the work
    baseline = await auto.remind(
        "Run train.py and report val_loss",
        schema={"val_loss": "float"}
    )
    best = baseline["val_loss"]

    for i in range(20):
        result = await auto.remind(
            f"Experiment {i+1}: try to beat val_loss={best}. "
            "Edit train.py, commit, run, report.",
            schema={"val_loss": "float", "description": "str"}
        )

        if result["val_loss"] < best:
            best = result["val_loss"]
            await auto.remind(f"Good, improved to {best}. Keep it.")
        else:
            await auto.remind("Didn't improve. Revert: git reset --hard HEAD~1")

        if (i + 1) % 5 == 0:
            await auto.remind("Reflect: what's working? What to try next?")
```

No imports needed — the `auto` object is passed to `main`.

## API

```python
result = await auto.remind(instruction)              # returns str
result = await auto.remind(instruction, schema={})   # returns dict
result = await auto.task(instruction, to="agent")    # dispatch to another agent
auto.agent(name, cwd=None)                           # declare an agent
```

### `auto.remind(instruction, schema=None, timeout=None)`
Send yourself a message. Your session executes the instruction and returns the result.

### `auto.task(instruction, to, schema=None, timeout=None)`
Assign work to another agent via `claude -p` subprocess.

### `auto.agent(name, cwd=None)`
Declare an agent before first use. Optional — `task(to="name")` auto-creates agents.

If JSON parsing fails, it retries up to 2 times automatically.

## Monitor and control

```bash
auto-run status    # process state + recent logs (non-blocking)
auto-run stop      # kill the program
```

## State tracking (optional)

```python
from auto import state

async def main(auto):
    state.set("status", "running")
    for i in range(100):
        result = await auto.remind(f"experiment {i}", schema={"score": "float"})
        state.update({"step": i, "score": result["score"]})
    state.set("status", "done")
```

Progress visible via `auto-run status` or `cat auto-state.json`.

## Patterns

### Optimization loop
```python
async def main(auto):
    best = 999
    for i in range(20):
        r = await auto.remind(f"Try to beat {best}", schema={"loss": "float"})
        if r["loss"] < best:
            best = r["loss"]
        else:
            await auto.remind("Revert")
```

### Multi-agent
```python
async def main(auto):
    auto.agent("researcher", cwd="/home/user/research")
    auto.agent("coder", cwd="/home/user/project")

    findings = await auto.task("Survey recent papers on X", to="researcher")
    await auto.task(f"Implement based on: {findings}", to="coder")
```

### Error recovery
```python
async def main(auto):
    for i in range(20):
        try:
            r = await auto.remind(f"Experiment {i}", schema={"loss": "float"})
        except Exception as e:
            await auto.remind(f"Failed: {e}. Try a simpler approach.")
```

### Periodic reflection
```python
async def main(auto):
    for i in range(100):
        await auto.remind(f"Experiment {i}")
        if (i + 1) % 10 == 0:
            await auto.remind("Reflect on last 10 experiments. Adjust strategy.")
```

## Key insight

Each `auto.remind()` is YOUR full turn — you use all your tools (Bash, Read, Edit, etc.) to execute the instruction. `auto.task()` dispatches to other agents. The Python program decides what comes next based on results. You keep full conversation memory across all steps.
