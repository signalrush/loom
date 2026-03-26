# Auto Orchestration Language Design

## Problem

The current `auto` framework has one primitive (`step()`) that controls a single Claude Code session. This is insufficient for:

1. **Swarm control** — orchestrating multiple independent Claude Code agents
2. **Self-messaging clarity** — the agent writing the program should understand it's sending itself reminders
3. **Monitoring** — the program runs in the background; without explicit reminders, the agent loses track of progress

## API

Three functions. That's the entire language.

```python
auto.remind(instruction, schema=None, timeout=None) -> str | dict
auto.task(instruction, to, schema=None, timeout=None) -> str | dict
auto.agent(name, cwd=None) -> None
```

### `auto.remind(instruction, schema=None, timeout=None)`

Send yourself a message. Your Claude Code session executes the instruction and returns the result.

This is the program's way of nudging its own agent: "go do this, then come back." Without a `remind`, the agent goes idle — the program is the agent's external attention loop.

- **`instruction`** (str): Natural language. What to do.
- **`schema`** (dict, optional): Keys are field names, values are type descriptions (e.g. `"float"`, `"list of strings"`). These are LLM guidance only — the implementation validates that the response is valid JSON, but does not enforce that field types match the descriptions. Returns parsed dict instead of str. Retries up to 3 times on JSON parse failure.
- **`timeout`** (int, optional): Seconds. Raises `TimeoutError` if exceeded. Default: no timeout (polls forever).

**Returns:** `str` (default) or `dict` (if schema provided).

### `auto.task(instruction, to, schema=None, timeout=None)`

Assign work to another agent. That agent's Claude Code session executes the instruction and returns the result.

- **`instruction`** (str): Natural language. What to do.
- **`to`** (str, required): Agent name. Must match a declared or implicitly created agent.
- **`schema`** (dict, optional): Same as `remind`.
- **`timeout`** (int, optional): Same as `remind`.

**Returns:** `str` (default) or `dict` (if schema provided).

### `auto.agent(name, cwd=None)`

Declare an agent before first use. Optional — calling `task(to="name")` without prior declaration creates an agent with default config.

- **`name`** (str): Unique agent identifier. Used for state files, logs, and session naming.
- **`cwd`** (str, optional): Working directory for the agent's Claude Code session. Defaults to the program's cwd.
- Future: additional kwargs like `allowed_tools` can be added without breaking the API.

**Returns:** None. Configuration only.

## Program Entry Point

```python
async def main(auto):
    ...
```

The `auto` object provides `remind()`, `task()`, and `agent()`. Programs are plain Python async functions. Control flow (loops, conditionals, error handling, variables) is Python.

## Agent Lifecycle

- **Declaration:** `auto.agent("coder", cwd="/app")` stores config. No session is started yet.
- **Lazy creation:** The agent's Claude Code session starts on the first `task(to="coder")` call.
- **Persistence:** Same name = same session. Context accumulates across `task()` calls to the same agent. The first `claude -p` call returns a `session_id` (UUID) in its JSON output. This UUID is stored in `{name}.json` in the global run folder (`~/.auto/run-{ts}-{pid}/`). Subsequent calls use `claude -p --resume "{session_id}"` with the stored UUID.
- **Undeclared agents:** `auto.task("do X", to="helper")` without a prior `agent("helper")` call creates one with default config. Forgiving by design — the LLM doesn't need to remember to declare.
- **Redeclaration:** Calling `auto.agent("coder", ...)` a second time is a no-op. First declaration wins. To use different config, use a different name.
- **Cleanup:** All agent subprocess PIDs are tracked in the run folder. On program exit (done, error, or SIGTERM), all tracked subprocesses are killed via SIGTERM. An `atexit` handler ensures cleanup even on unhandled exceptions.

## IPC Architecture

### Self (`remind`)

Same mechanism as the current `step()`. A stop hook installed in the Claude Code TUI session intercepts turn endings. The Python program writes instructions to a state file; the hook reads them and injects them as Claude's next turn. Claude's response is captured from the transcript and written back.

Protocol: `pending -> running -> responded -> pending -> ...`

### Others (`task`)

Each agent runs as a `claude -p --output-format json` subprocess.

- **First call:** `claude -p "instruction" --output-format json --cwd "/path"`. Returns JSON with `result` (response text) and `session_id` (UUID). The `session_id` is stored in `{name}.json`.
- **Subsequent calls:** `claude -p "instruction" --resume "{session_id}" --output-format json`. Context persists via the stored session ID.
- **Tool access:** Full tool access (bash, file edit, search) works in `-p` mode. Uses `--allowedTools "Bash,Read,Edit,Write,Glob,Grep"` by default. Can be restricted via `auto.agent("name", allowed_tools=[...])` (future).
- **Permissions:** Uses `--dangerously-skip-permissions` for non-interactive execution. The program author is responsible for scoping what agents can do via instructions.

No stop hook needed for `task` agents — `claude -p` is synchronous (blocks until done, returns JSON).

**Response extraction:** `claude -p --output-format json` returns `{"result": "...", "session_id": "uuid", ...}`. The implementation parses `result` as the return value of `task()`. This output format is a Claude Code CLI contract; if it changes, `task()` parsing must be updated.

**Error handling for `task()`:**
- Non-zero exit from `claude -p`: raises `RuntimeError` with stderr content.
- Malformed JSON output: raises `RuntimeError`.
- Subprocess killed mid-execution: raises `RuntimeError`. The session may still be resumable on next call.
- `--resume` with invalid/expired session ID: falls back to starting a new session (loses context). Logs a warning.
- `timeout` exceeded: subprocess is killed with SIGTERM, then SIGKILL after 5s. Raises `TimeoutError`. Session may still be resumable.

### Data Flow

```
auto.remind("do X"):
  Python  --[write pending]--> ~/.auto/latest/self.json --[hook reads]--> Claude TUI
  Claude TUI --[hook writes responded]--> ~/.auto/latest/self.json --[Python reads]--> return

auto.task("do X", to="coder"):
  Python  --[read coder.json for session_id]
          --[subprocess]--> claude -p --resume "{session_id}" "do X"
  claude -p --[executes, returns JSON]--> Python
          --[store session_id in coder.json]
          --[parse result]--> return
```

## File Layout

State and logs live in the **global** `~/.auto/` directory, not in the project. This prevents state files from polluting the project, being seen by agents, or being accidentally committed.

Each program run gets its own folder:

```
~/.auto/
  run-20260326-150000-12345/       # {timestamp}-{pid}
    self.json                       # remind() state
    coder.json                      # task(to="coder") state
    reviewer.json                   # task(to="reviewer") state
    logs/
      self.log                      # self log (stdout redirect)
      coder.log                     # coder subprocess output
      reviewer.log                  # reviewer subprocess output
  latest -> run-20260326-150000-12345/   # symlink to current run
```

- Old runs persist for debugging (never overwritten).
- PID in folder name ties it to the process.
- Symlink `latest/` lets CLI commands find the current run.
- No project-local files — nothing to `.gitignore`.

### Agent State File Schema (`{name}.json`)

```json
{
  "name": "coder",
  "session_id": "uuid-from-claude-cli",
  "status": "idle | running | error",
  "step_number": 3,
  "last_instruction": "fix the auth bug",
  "cwd": "/app",
  "pid": 54321,
  "updated_at": "2026-03-26T15:00:00Z"
}
```

For `self.json`, `session_id` is not used (IPC is via stop hook, not `claude -p`). The status field drives `auto-run status` output.

All state files live under `~/.auto/`, resolved via `Path.home() / ".auto"` in Python and `$HOME/.auto` in bash.

### Migration from Current File Layout

The current project-local layout (`.claude/auto-loop.json`, `.claude/logs/`, `.claude/auto.pid`) is replaced by the global per-run folder structure in `~/.auto/`. The stop hook path in `stop-hook.sh` changes from the hardcoded `.claude/auto-loop.json` to `$HOME/.auto/latest/self.json`. The `latest` symlink ensures the hook always finds the current run. The old project-local files are no longer written; existing programs using `step()` will use the new paths via the `auto.remind()` alias.

## CLI

```bash
auto-run program.py          # start program, creates run folder
auto-run status              # shows all agent states from latest/
auto-run log                 # tails latest/logs/self.log
auto-run log coder           # tails latest/logs/coder.log
auto-run stop                # kills program + all agent sessions
```

`auto-run status` output:

```
=== Run: 20260326-150000-12345 ===
self:      waiting (step 3)     "check CI status"
coder:     running (step 1)     "fix the auth bug"
reviewer:  idle
```

## Agent Reference (What the LLM Sees)

The entire reference for an LLM writing programs:

```
You are writing a background program that will keep reminding you
(or other agents) what to do next. Each remind/task pauses the
program until the work is done. If you don't remind yourself,
nothing happens — you lose track.

auto.remind(instruction)                — remind yourself to do something
auto.remind(instruction, schema={...})  — get structured data back
auto.task(instruction, to="agent")      — assign work to another agent
auto.task(instruction, to="agent", schema={...}) — structured
auto.agent(name, cwd=path)             — declare an agent (optional)

timeout= on any call raises TimeoutError after N seconds.
Control flow is Python: for, if, try/except, variables.
```

## Example: Code Review Pipeline

```python
async def main(auto):
    auto.agent("coder", cwd="/app")
    auto.agent("reviewer")

    # Remind yourself to analyze
    plan = await auto.remind(
        "Analyze the repo and suggest 3 improvements",
        schema={"improvements": "list of strings"})

    for item in plan["improvements"]:
        # Assign implementation to coder
        code = await auto.task(f"Implement: {item}", to="coder")

        # Assign review
        review = await auto.task(
            f"Review this change: {code}", to="reviewer",
            schema={"approved": "bool", "issues": "list"})

        if not review["approved"]:
            await auto.task(f"Fix: {review['issues']}", to="coder")

    # Remind yourself to wrap up
    await auto.remind("Commit everything and write a changelog")
```

## Example: Autonomous Research

```python
async def main(auto):
    auto.agent("experimenter", cwd="/ml-project")

    best_loss = float("inf")

    for i in range(20):
        result = await auto.task(
            f"Experiment {i+1}: beat val_loss={best_loss}. "
            f"Edit model, train, report results.",
            to="experimenter",
            schema={"val_loss": "float", "description": "str"})

        if result["val_loss"] < best_loss:
            best_loss = result["val_loss"]
            await auto.remind(f"New best: {best_loss}. Log it.")
        else:
            await auto.task("Revert: git reset --hard HEAD~1",
                           to="experimenter")

        # Periodic reflection
        if (i + 1) % 5 == 0:
            await auto.remind(
                f"Progress: {i+1}/20 experiments, best={best_loss}. "
                f"What strategy should we try next?")
```

## Dependencies

Zero new dependencies. Same as today:
- `remind`: bash stop hook + jq (existing mechanism)
- `task`: `claude -p` subprocess (Claude Code CLI, already installed)
- State files: stdlib Python (json, tempfile, os)

## What Is NOT in the API

- **No parallel primitive** — use sequential calls to different agents, or tell self to use sub-agents in the instruction
- **No checkpoint/resume** — use the existing `auto.state` module if needed
- **No scheduling** — use cron + `auto-run`
- **No chains/pipes** — use Python variables
- **No human-in-the-loop gates** — future work
- **No agent-to-agent direct communication** — all data flows through the Python program

## Migration from Current API

| Current | New |
|---|---|
| `async def main(step)` | `async def main(auto)` |
| `await step("do X")` | `await auto.remind("do X")` |
| `await step("do X", schema={...})` | `await auto.remind("do X", schema={...})` |
| N/A | `await auto.task("do X", to="agent")` |
| N/A | `auto.agent("name", cwd=...)` |

The `step()` function continues to work as an alias for `auto.remind()` during migration.
