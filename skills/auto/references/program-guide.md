# Writing Auto Programs

## The basics

An auto program is a Python file with one function:

```python
async def main(auto):
    result = await auto.remind("do something")
```

`auto` is passed in by the runtime. `remind()` sends an instruction into your session and returns the result. `task()` dispatches work to other agents. You keep your memory across all steps.

## API reference

### `auto.remind(instruction, schema=None, timeout=None)`

Send yourself a message. Your session executes the instruction.

```python
# Plain text response
result = await auto.remind("list files in current directory")
# result is a string

# Structured response
result = await auto.remind("report metrics", schema={"loss": "float", "acc": "float"})
# result is a dict: {"loss": 0.23, "acc": 0.91}
```

### `auto.task(instruction, to, schema=None, timeout=None)`

Assign work to another agent:

```python
result = await auto.task("run the full test suite", to="tester")
```

### `auto.agent(name, cwd=None)`

Declare an agent before first use (optional — `task()` auto-creates):

```python
auto.agent("researcher", cwd="/home/user/papers")
```

### When to use schema

Use `schema` when your Python code needs to branch on the result:

```python
r = await auto.remind("run experiment", schema={"improved": "bool", "score": "float"})
if r["improved"]:
    best = r["score"]
```

Skip schema when you just want the model to do something:

```python
await auto.remind("revert the last commit")
await auto.remind("read the error log and fix the bug")
```

## State management

For programs that run long (hours/days), track progress with `auto.state`:

```python
from auto import state

async def main(auto):
    state.set("status", "running")
    state.update({"best": 999, "step": 0})

    # ... later
    state.update({"best": 0.23, "step": 42})

    # Read back
    current_best = state.get("best")  # 0.23
    all_state = state.get()  # full dict
```

State persists in `auto-state.json`. Survives program restarts.

## Control flow patterns

### Fixed loop
```python
async def main(auto):
    for i in range(100):
        await auto.remind(f"iteration {i}: improve the code")
```

### While loop with condition
```python
async def main(auto):
    score = 0
    while score < 0.95:
        r = await auto.remind("improve accuracy", schema={"score": "float"})
        score = r["score"]
```

### Branching
```python
async def main(auto):
    r = await auto.remind("analyze the codebase", schema={"needs_refactor": "bool"})
    if r["needs_refactor"]:
        await auto.remind("refactor the main module")
    else:
        await auto.remind("add the new feature directly")
```

### Error recovery
```python
async def main(auto):
    retries = 0
    while retries < 3:
        try:
            r = await auto.remind("deploy and test", schema={"passed": "bool"})
            if r["passed"]:
                break
        except Exception:
            retries += 1
            await auto.remind("something went wrong, diagnose and fix")
```

### Multi-agent
```python
async def main(auto):
    auto.agent("builder", cwd="/home/user/project")
    auto.agent("reviewer", cwd="/home/user/project")

    await auto.task("implement the feature from TODO.md", to="builder")
    review = await auto.task("review the latest changes", to="reviewer")
    await auto.task(f"Address review feedback: {review}", to="builder")
```

### Periodic replanning
```python
async def main(auto):
    for i in range(100):
        await auto.remind(f"experiment {i}")
        if (i + 1) % 10 == 0:
            await auto.remind(
                "Pause. Review the last 10 experiments. "
                "What patterns are working? Adjust strategy."
            )
```

## Tips

1. **remind() = you acting.** It's you, doing one thing in your own session.
2. **task() = delegation.** Dispatches work to another agent via `claude -p`.
3. **Schema = bridge to Python.** Use it when Python needs to make decisions based on results.
4. **Context accumulates.** You remember everything. No need to repeat context.
5. **State = crash safety.** Use `auto.state` for anything you'd want to survive a restart.
6. **Steer by restart.** Kill, edit program.py, restart. State persists.
7. **Keep steps focused.** One clear instruction per step. Let the model figure out the details.
