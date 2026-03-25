# Writing Loom Programs

## The basics

A loom program is a Python file with one function:

```python
async def main(step):
    result = await step("do something")
```

`step` is passed in by the runtime. Each call sends an instruction into your session and returns the result. You keep your memory across all steps.

## step() reference

```python
# Plain text response
result = await step("list files in current directory")
# result is a string

# Structured response
result = await step("report metrics", schema={"loss": "float", "acc": "float"})
# result is a dict: {"loss": 0.23, "acc": 0.91}
```

### When to use schema

Use `schema` when your Python code needs to branch on the result:

```python
r = await step("run experiment", schema={"improved": "bool", "score": "float"})
if r["improved"]:
    best = r["score"]
```

Skip schema when you just want the model to do something:

```python
await step("revert the last commit")
await step("read the error log and fix the bug")
```

## State management

For programs that run long (hours/days), track progress with `loom.state`:

```python
from loom import state

async def main(step):
    state.set("status", "running")
    state.update({"best": 999, "step": 0})
    
    # ... later
    state.update({"best": 0.23, "step": 42})
    
    # Read back
    current_best = state.get("best")  # 0.23
    all_state = state.get()  # full dict
```

State persists in `loom-state.json`. Survives program restarts.

## Control flow patterns

### Fixed loop
```python
async def main(step):
    for i in range(100):
        await step(f"iteration {i}: improve the code")
```

### While loop with condition
```python
async def main(step):
    score = 0
    while score < 0.95:
        r = await step("improve accuracy", schema={"score": "float"})
        score = r["score"]
```

### Branching
```python
async def main(step):
    r = await step("analyze the codebase", schema={"needs_refactor": "bool"})
    if r["needs_refactor"]:
        await step("refactor the main module")
    else:
        await step("add the new feature directly")
```

### Error recovery
```python
async def main(step):
    retries = 0
    while retries < 3:
        try:
            r = await step("deploy and test", schema={"passed": "bool"})
            if r["passed"]:
                break
        except Exception:
            retries += 1
            await step("something went wrong, diagnose and fix")
```

### Periodic replanning
```python
async def main(step):
    for i in range(100):
        await step(f"experiment {i}")
        if (i + 1) % 10 == 0:
            await step(
                "Pause. Review the last 10 experiments. "
                "What patterns are working? Adjust strategy."
            )
```

## Tips

1. **Step = you acting.** Don't think of it as calling a sub-agent. It's you, doing one thing.
2. **Schema = bridge to Python.** Use it when Python needs to make decisions based on results.
3. **Context accumulates.** You remember everything. No need to repeat context.
4. **State = crash safety.** Use `loom.state` for anything you'd want to survive a restart.
5. **Steer by restart.** Kill, edit program.py, restart. State persists.
6. **Keep steps focused.** One clear instruction per step. Let the model figure out the details.
