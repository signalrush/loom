# Step: A Minimal Primitive for Self-Controlling Agents

## Abstract

We propose `step()` — a single primitive that lets an LLM programmatically control its own execution flow and context. The model writes Python code that composes `step()` calls. Each `step()` is a full agent turn (with tool access). Python provides the control flow (loops, conditionals, recursion). The result: every known agent architecture pattern (ReAct, RLM, Slate threads, self-guardrailing) emerges as a special case of `step()` + Python.

## Motivation

### The Problem with Current Agent Architectures

Every existing agent architecture hardcodes a specific execution pattern into the harness:

| Architecture | What the harness controls |
|---|---|
| ReAct | The tool loop — model responds, harness re-calls |
| RLM | Script execution — model writes code, harness runs it fire-and-forget |
| Slate | Thread dispatch — harness manages episodes and orchestration |
| Task Trees | Tree traversal — harness walks nodes, gates completion |
| Subagents | Process lifecycle — harness spawns, routes messages, collects results |

The model has no control over its own execution. The harness decides when the model runs, what it sees, and when it stops. The model is a passenger.

### The Insight

What if the model could write the harness? Not a new harness — just a Python program that calls `step()`. The model controls:

- **Execution flow** — loops, conditionals, sequencing
- **Context** — what each step sees
- **When to stop** — termination conditions in code, not in the model's "memory"
- **When to replan** — just call another `step()` to re-evaluate

Python provides everything else (variables, data structures, error handling) for free. The model already knows Python better than any DSL we could invent.

### Why Not Just RLM?

RLM (Recursive Language Models) is the closest prior work. It gives the model a Python REPL with `prompt_model()`. The key differences:

1. **RLM scripts are fire-and-forget.** The model writes a script, it runs to completion, the result returns to an outer model. That outer model accumulates context across scripts — back to the same degradation problem.

2. **`step()` has no outer model.** The program IS the top-level agent. There's nothing above it accumulating context.

3. **`step()` is a full agent turn, not a text completion.** Each step has tool access — it can read files, run commands, edit code. `prompt_model()` in RLM is just text in, text out.

4. **Replanning is in-band.** Not "outer model reads result and writes new script" but `new_plan = step("replan", state)` as a line in the running program.

## Design

### The Primitive

```python
result = step(instruction, context=None, schema=None)
```

- **`instruction`** (`str`) — What to do. Natural language.
- **`context`** (`any`, optional) — What this step can see. Previous results, file contents, state. If omitted, minimal default context.
- **`schema`** (`dict`, optional) — If provided, the step must return structured output matching this schema. If omitted, returns a string.

That's it. One function.

### What Happens Inside a `step()`

Each `step()` invocation triggers a full agent turn:

1. The instruction and context are assembled into a prompt
2. If a schema is provided, output format instructions are appended
3. The model runs with full tool access (file read/write, bash, etc.)
4. The model's response is returned — as a string, or as a structured object if schema was specified

The model inside a step doesn't know it's inside a step. It's just a normal agent turn. It can use tools, think, act. The `step()` boundary is invisible from inside.

### Schema: Optional Structure

Schema is the bridge between natural language and Python control flow.

**Without schema** — the result is a string. Useful when the next consumer is another `step()` (which can read natural language) or when the step's value is its side effects (editing a file, running a command).

**With schema** — the result is a structured object. Useful when Python code needs to branch on the result (`if result.val_bpb < best`).

The model writing the program decides which steps need schema. This is a judgment call, not a system requirement. The principle: **use schema only when Python needs to understand the output. If only another step needs to understand it, string is fine.**

Schema implementation: when provided, the prompt for that step is augmented with output format instructions. The runtime parses and validates. This is equivalent to what structured output / function calling already does.

### Step Granularity is Free

The same task can be expressed at any granularity:

**One fat step (program.md style):**
```python
while True:
    step("propose experiment, edit train.py, run it, if improved keep else revert")
```

**Many thin steps (full Python control):**
```python
while True:
    idea = step("propose experiment", schema={"description": str})
    step(f"edit train.py: {idea.description}")
    result = step("run train.py, report metrics", schema={"val_bpb": float})
    if result.val_bpb < best:
        step("commit and log keep")
        best = result.val_bpb
    else:
        step("revert and log discard")
```

**Mixed (pragmatic):**
```python
while True:
    result = step("propose and run one experiment",
                  schema={"val_bpb": float, "description": str})
    if result.val_bpb < best:
        best = result.val_bpb
    # replan every 10 experiments
    if count % 10 == 0:
        step("review results.tsv, adjust strategy")
```

The model chooses granularity based on the task. Simple tasks use fat steps. Tasks needing precise control use thin steps. This is a spectrum, not a binary.

## How `step()` Subsumes Existing Patterns

### ReAct Loop

```python
while True:
    result = step("look at current state, take next action", state)
    state = result
    if step("am I done?", state, schema={"done": bool}).done:
        break
```

ReAct is a while loop with one step per iteration. The difference: the loop is in Python (guaranteed to continue), not in the model's memory (might forget to continue).

### RLM / Recursive Decomposition

```python
def solve(task, depth=0):
    if depth >= max_depth:
        return step(f"solve directly: {task}")
    parts = step(f"decompose: {task}", schema={"subtasks": list})
    results = [solve(s, depth+1) for s in parts.subtasks]
    return step(f"synthesize results for: {task}", results)
```

Recursion is just Python recursion. Depth limit is a variable, not something the model has to remember. Each level gets its own step with fresh context.

### Self-Guardrailing

```python
action = step("what should I do next?", state)
safe = step(f"is this safe? {action}", schema={"safe": bool})
if safe.safe:
    step(action)
else:
    step("propose safer alternative", state)
```

Guardrailing is just an if-statement between two steps. The check can't be skipped because it's in code, not in the model's reasoning.

### Autoresearch (Karpathy)

The `program.md` pattern — expressed as an actual program:

```python
baseline = step("run train.py as-is, report val_bpb",
                schema={"val_bpb": float})
best = baseline.val_bpb

while True:
    result = step(
        "propose and run one experiment on train.py, "
        "commit before running, report result",
        {"best_so_far": best, "results": read("results.tsv")},
        schema={"val_bpb": float, "description": str, "status": str}
    )

    if result.val_bpb < best:
        best = result.val_bpb

    if experiments % 10 == 0:
        step("review results.tsv, reflect on what's working, adjust strategy")
```

vs. `program.md` which says the same thing in English and relies on the model to maintain the loop, remember the best score, and count experiments. Here, Python does all of that reliably.

## Key Properties

1. **One primitive.** `step()` is the only new concept. Everything else is Python.

2. **Intuitive for models.** Models are heavily trained on Python. Writing programs that compose `step()` calls is in-distribution.

3. **State doesn't degrade.** Python variables don't have a dumb zone. `best` at iteration 1000 is as accessible as at iteration 1.

4. **Control flow is real.** `while True` actually loops. `if/else` actually branches. No reliance on the model "remembering" to continue or check a condition.

5. **Each step is fresh.** Every `step()` gets a clean context window with only what the code passes in. Step 100 is as lucid as step 1.

6. **Schema is opt-in.** Default is string (simple, flexible). Schema only where Python needs to understand the output.

7. **Granularity is free.** One big step or many small steps — the model chooses based on the task.

8. **Replanning is natural.** Just call `step("should I change my approach?", state)` anywhere in the code. No special mechanism.

## User Steering (Interrupts)

A long-running program needs to be steerable. The user must be able to redirect the agent mid-execution without the program needing to anticipate interrupts.

### Design: Interrupts Are Transparent to the Program

When a user sends a message while a step is running:

1. The runtime stops the current step
2. The runtime runs a new step: "User said: `{message}`. You were in the middle of: `{original_instruction}`. Handle the user's input and produce the expected output."
3. If the original step had a schema, the new step must also return that schema
4. The result is returned to the program as if the original step completed normally

The program never knows an interrupt happened. From its perspective, `step()` was called and a result came back. The runtime handled everything in between.

### Why Transparent?

- The program doesn't need try/except for interrupts — simpler code
- Schema contracts are always satisfied — the program can't crash from a missing return value
- The user's message is naturally incorporated — the replacement step sees both the user's input and the original task context
- Works for any program, even ones that weren't written with interrupts in mind

### What This Means in Practice

User writes "try larger batch size" while the agent is mid-experiment. The runtime:
1. Stops the current step
2. Asks the model: "User wants larger batch size. You were running an experiment. Adjust and report results." (with the same schema)
3. Returns the result to the program's `while True` loop
4. Program continues as normal, now with the user's direction incorporated

The program is a plan. The user can steer it at any time. The plan doesn't need to know about steering.

## Implementation: OpenCode as Runtime

### Why OpenCode

OpenCode is an open-source coding agent with a client/server architecture. It provides:

- **Server mode** (`opencode serve`) — headless HTTP server with OpenAPI spec
- **Python SDK** (`opencode-agent-sdk`) — async client with `query()` / `receive_response()` 
- **Full tool access** — bash, file read/write, LSP, MCP servers
- **Session management** — create, resume, and manage sessions via API
- **Provider agnostic** — works with Anthropic, OpenAI, Google, local models
- **Hooks** — intercept tool execution at `PreToolUse` and `Stop` events

This maps directly onto `step()`: each step is a `query()` → `receive_response()` cycle against the OpenCode server.

### step() Implementation

```python
from opencode_agent_sdk import SDKClient, AgentOptions, AssistantMessage, TextBlock
import json

class StepRuntime:
    def __init__(self, server_url="http://localhost:54321", cwd="."):
        self.server_url = server_url
        self.cwd = cwd

    async def step(self, instruction, context=None, schema=None):
        # Build prompt
        prompt = instruction
        if context is not None:
            prompt = f"Context:\n{context}\n\nTask: {instruction}"
        if schema is not None:
            prompt += f"\n\nYou must return valid JSON matching this schema: {json.dumps(schema)}"

        # Each step = fresh session (fresh context window)
        client = SDKClient(options=AgentOptions(
            server_url=self.server_url,
            cwd=self.cwd,
        ))
        await client.connect()
        await client.query(prompt)

        # Collect response
        result = ""
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result += block.text

        await client.disconnect()

        # Parse schema if needed
        if schema is not None:
            return json.loads(result)  # runtime validates against schema
        return result
```

### Key Design Decisions

**Fresh session per step.** Each `step()` creates a new session. This guarantees fresh context — step 100 is as clean as step 1. Cross-step state is passed explicitly through the `context` parameter, not accumulated in a context window.

**OpenCode handles tool execution.** The model inside a step has full access to bash, file editing, etc. The `step()` caller doesn't need to know or manage tools. This is what makes `step()` a full agent turn, not just a text completion.

**Schema via prompt engineering.** When schema is provided, it's appended to the prompt as output format instructions. OpenCode's model produces the structured output. The runtime parses and validates. No special structured output API needed — works with any model.

**Model selection via AgentOptions.** Different steps can use different models by passing `model` to AgentOptions. This is how you'd do heterogeneous multi-model orchestration if needed later.

### User Steering via Hooks

OpenCode's hook system enables the interrupt mechanism:

```python
async def interrupt_hook(input_data, tool_use_id, context):
    if pending_user_message:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"User interrupt: {pending_user_message}",
            }
        }
    return {}
```

When a user sends a message during a running step, the hook denies the current tool call. The runtime then stops the step and re-runs it with the user's message incorporated, returning a result that satisfies the original schema contract.

### Autoresearch Example with OpenCode

```python
import asyncio

async def main():
    rt = StepRuntime(server_url="http://localhost:54321", cwd="/path/to/autoresearch")

    # Baseline
    baseline = await rt.step(
        "Run `uv run train.py > run.log 2>&1`, then `grep '^val_bpb:' run.log`. Report the val_bpb.",
        schema={"val_bpb": "float"}
    )
    best = baseline["val_bpb"]

    count = 0
    while True:
        count += 1
        result = await rt.step(
            "Propose one experiment. Edit train.py, git commit, run it, report results.",
            context={"best_so_far": best, "experiment_number": count},
            schema={"val_bpb": "float", "description": "str", "status": "str"}
        )

        if result["val_bpb"] < best:
            best = result["val_bpb"]
            await rt.step(f"Log keep to results.tsv: {result['description']}, val_bpb={result['val_bpb']}")
        else:
            await rt.step(f"Log discard to results.tsv: {result['description']}. Git reset to previous commit.")

        if count % 10 == 0:
            await rt.step("Read results.tsv. Reflect on what directions are working. Adjust strategy for next experiments.")

asyncio.run(main())
```

This program runs indefinitely. Each step is a fresh context. The Python loop handles state (best score, count), branching (keep/discard), and periodic replanning. OpenCode handles the actual coding agent work inside each step.

## Integration: Loom as an Agent Skill

### Architecture

```
opencode serve --port 54321
       │
       ├── TUI attaches (interactive view)
       │
       ├── loom program.py connects (automated steps)
       │
       └── web UI (optional)
```

The OpenCode server is the brain. The TUI is a window. Loom programs are another client. They share the same backend, tools, and filesystem.

### How it works

The model inside the TUI:

1. **Installs the skill** — `pip install loom-agent` (or it's pre-installed in the environment)
2. **Reads the skill instructions** — learns how to write `step()` programs
3. **Writes `program.py`** — a Python script composing `step()` calls
4. **Runs `loom-run program.py`** — a wrapper script that handles plumbing
5. **Checks progress** — reads `loom-state.json` or `loom-run status`

### `loom-run` — the wrapper script

```bash
loom-run program.py    # start a loom program in background
loom-run status        # show running state, last result
loom-run log           # tail the output log
loom-run stop          # kill a running program
```

What `loom-run` does internally:

1. **Discovers the server port** — reads from `OPENCODE_SERVER_URL` env var, or from OpenCode's config/state files, or accepts `--port` explicitly
2. **Ensures loom is installed** — `pip install loom-agent` if needed
3. **Sets `LOOM_SERVER_URL`** — so `step()` connects to the existing server
4. **Runs program.py in background** — `nohup python program.py > loom.log 2>&1 &`, writes PID to `loom.pid`
5. **program.py writes progress** — structured state to `loom-state.json`

### State reporting

Programs use a `state` helper to write progress:

```python
from loom import step, state

async def main():
    state.set("status", "running")
    
    baseline = await step("run train.py", schema={"loss": "float"})
    state.update({"best_loss": baseline["loss"], "step": 0})
    
    for i in range(100):
        result = await step(
            "propose and run an experiment",
            context=state.get(),
            schema={"loss": "float", "description": "str"}
        )
        if result["loss"] < state.get("best_loss"):
            state.update({"best_loss": result["loss"], "step": i + 1})
    
    state.set("status", "done")
```

The model in the TUI can `cat loom-state.json` at any time to see:
```json
{"status": "running", "best_loss": 0.23, "step": 7}
```

### Steering a running program

The model can steer by:

1. **Kill and restart** — `loom-run stop`, edit `program.py`, `loom-run program.py`
2. **Edit program.py while running** — if the program re-reads itself (self-rewrite pattern), changes take effect on next iteration
3. **Write to a control file** — program.py watches `loom-control.json`, model writes directives to it

Option 1 is simplest and sufficient. The program is cheap to restart because state is in files, not in memory.

### What ships as a skill

```
skills/loom/
  SKILL.md              # instructions for the model
  scripts/
    loom-run            # CLI wrapper (bash)
  references/
    program-guide.md    # how to write loom programs
    examples.md         # example programs
```

### Why this works

- **No MCP, no special protocol** — just Python + HTTP
- **No context pollution** — each step() is a fresh session, the TUI stays clean
- **Model writes the program** — full control over execution flow
- **Visible state** — JSON files the model can read anytime
- **Same server** — loom uses the same backend as the TUI, same tools, same filesystem
- **Steer by restart** — kill, edit, restart is simple and reliable

## Open Questions

1. ~~**Who writes the program?**~~ **Resolved: the model writes it.** The model generates the `step()` program on its first turn. It can also rewrite the program mid-run — the program is a file, and the model can edit it in any step. The next loop iteration picks up the new version.

2. **Error handling.** What happens when a step fails? Python try/except is the obvious answer, but does the model write good error handling?

3. **Context window within a step.** If a single step involves many tool calls, it still faces context accumulation within that step. Step boundaries help but don't eliminate the problem.

4. **Training.** Models aren't specifically trained to write `step()` programs. How much does this matter? Is Python fluency enough, or do you need RL on step-composition specifically?

## Conclusion

`step()` inverts the control relationship between model and harness. Instead of the harness deciding when and how the model runs, the model writes a program that decides. The harness becomes a runtime — it executes `step()` calls, but the model controls the flow.

Every prior agent architecture is a special case of this: a specific program the model could have written. `step()` lets the model choose the right program for the right task, at runtime, using the programming language it already knows best.
