# Loom Skill - Self-Controlling Agent Programs

## What is Loom?

Loom lets you write Python programs that control their own execution flow using the `step()` primitive. Instead of being limited to a single conversation, you can write programs that loop, branch, plan, and replan while maintaining state across hundreds or thousands of iterations.

Each `step()` call is a full agent turn with tool access, but the Python code around it controls when to continue, what to remember, and how to adapt.

## How to Use

### 1. Write a Loom Program

Create a Python file using `from loom import step, state`:

```python
# example_program.py
import asyncio
from loom import step, state

async def main():
    # Set initial status
    state.set("status", "starting")
    
    # Get baseline
    baseline = await step(
        "Run the current train.py and report the validation loss",
        schema={"val_loss": "float"}
    )
    
    # Track progress
    state.update({
        "best_loss": baseline["val_loss"],
        "experiments": 0,
        "status": "running"
    })
    
    # Main research loop
    while state.get("experiments") < 100:
        # Propose and run experiment
        result = await step(
            "Propose one experiment to improve the model. "
            "Edit train.py, test it, and report results.",
            context=state.get(),
            schema={"val_loss": "float", "description": "str", "kept": "bool"}
        )
        
        # Update state based on results
        experiments = state.get("experiments") + 1
        if result["val_loss"] < state.get("best_loss"):
            state.update({
                "best_loss": result["val_loss"],
                "experiments": experiments
            })
        else:
            state.set("experiments", experiments)
        
        # Replan every 10 experiments
        if experiments % 10 == 0:
            await step(
                "Review the results so far and adjust the research strategy",
                context=state.get()
            )
    
    state.set("status", "complete")

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Run Your Program

```bash
# Start the program in background
loom-run example_program.py

# Check progress anytime
loom-run status

# Watch live logs  
loom-run log

# Stop if needed
loom-run stop
```

### 3. Monitor and Steer

Your program writes structured state to `loom-state.json`:

```json
{
  "status": "running",
  "best_loss": 0.234,
  "experiments": 27
}
```

To steer a running program:

1. **Edit and restart**: `loom-run stop`, edit the `.py` file, `loom-run example_program.py`
2. **Check state anytime**: `cat loom-state.json` or `loom-run status`  
3. **Watch progress**: `loom-run log` shows live output

## Key Patterns

### Simple Loop with State Tracking

```python
from loom import step, state
import asyncio

async def main():
    state.set("iteration", 0)
    
    while state.get("iteration") < 50:
        result = await step(
            f"Run experiment #{state.get('iteration')}",
            schema={"success": "bool", "result": "str"}
        )
        
        if result["success"]:
            state.set("last_success", result["result"])
        
        state.set("iteration", state.get("iteration") + 1)

asyncio.run(main())
```

### Research with Error Handling  

```python
from loom import step, state
import asyncio

async def main():
    state.set("errors", 0)
    
    while state.get("errors") < 3:
        try:
            result = await step(
                "Propose and test a new approach",
                context=state.get(),
                schema={"val_metric": "float", "approach": "str"}
            )
            
            # Success - reset error count
            state.update({
                "last_result": result,
                "errors": 0
            })
            
        except Exception as e:
            # Handle failures gracefully
            error_count = state.get("errors") + 1
            state.set("errors", error_count)
            
            await step(f"Experiment failed: {e}. Propose a simpler approach.")

asyncio.run(main())
```

### Auto-Research with Replanning

```python
from loom import step, state
import asyncio

async def main():
    # Initialize
    baseline = await step("Get baseline metrics", schema={"score": "float"})
    state.update({
        "best_score": baseline["score"],
        "total_experiments": 0,
        "strategy": "initial"
    })
    
    # Research loop with adaptive strategy
    while state.get("total_experiments") < 200:
        # Run experiments in batches
        for i in range(10):
            result = await step(
                f"Run experiment using {state.get('strategy')} strategy",
                context=state.get(),
                schema={"score": "float", "description": "str"}
            )
            
            if result["score"] > state.get("best_score"):
                state.update({
                    "best_score": result["score"],
                    "best_description": result["description"]
                })
            
            state.set("total_experiments", state.get("total_experiments") + 1)
        
        # Replan strategy every 10 experiments
        new_strategy = await step(
            "Review the last 10 experiments and propose a new strategy",
            context=state.get(),
            schema={"strategy": "str", "reasoning": "str"}
        )
        
        state.set("strategy", new_strategy["strategy"])

asyncio.run(main())
```

## API Reference

### `step(instruction, context=None, schema=None)`

The core primitive for agent actions.

- **instruction** (`str`): What to do in natural language
- **context** (`any`, optional): Information for this step to see  
- **schema** (`dict`, optional): If provided, forces structured JSON output

Returns a string (default) or structured object (if schema provided).

### `state` module

Manages persistent state in `loom-state.json`:

- **`state.set(key, value)`**: Set a single key
- **`state.update(dict)`**: Merge a dictionary into state  
- **`state.get(key=None)`**: Get one key or entire state dict

State is thread-safe and persists across program restarts.

### `loom-run` commands

- **`loom-run program.py [--port PORT]`**: Start program in background
- **`loom-run status`**: Show process status + state + recent logs
- **`loom-run log`**: Tail the live log output
- **`loom-run stop`**: Kill the running program

## When to Use Loom

**Perfect for:**
- Long-running research and optimization
- Multi-step workflows with branching logic
- Programs that need to adapt strategy over time
- Tasks requiring hundreds of iterations
- Experiments that run for hours or days

**Not ideal for:**
- Simple one-shot tasks (just use regular conversation)  
- Real-time interactions requiring immediate response
- Tasks that don't benefit from persistent state

## Tips

1. **Use structured state**: Keep important data in `state.update({...})` so it survives restarts
2. **Schema where Python needs it**: Use `schema=` when your code needs to branch on the results  
3. **Replan periodically**: Add replanning steps every N iterations to adapt strategy
4. **Handle errors**: Wrap `step()` calls in try/except for robust programs
5. **Start simple**: Begin with a basic loop, then add complexity as needed

## Getting Help

- Read `references/program-guide.md` for detailed programming patterns
- Check `examples/` directory for complete example programs
- Use `loom-run status` to debug running programs
- Edit and restart programs freely - state persists in JSON files