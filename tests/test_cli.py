"""Tests for auto-run CLI, especially hook setup.

These tests verify that the hook is installed into the CORRECT file
that Claude Code actually reads. This is the test that would have
caught the hooks.json vs settings.local.json bug.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# --- Setup hook tests ---

def test_setup_writes_to_settings_local_json(tmp_path):
    """CRITICAL: Hook must go in settings.local.json, NOT hooks.json.

    Claude Code reads project hooks from .claude/settings.local.json.
    It does NOT read .claude/hooks.json (that's for plugins only).
    """
    os.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()

    from auto.cli import _setup_hook
    _setup_hook()

    # MUST exist
    settings_file = tmp_path / ".claude" / "settings.local.json"
    assert settings_file.exists(), "Hook was not written to .claude/settings.local.json"

    # MUST NOT exist
    hooks_file = tmp_path / ".claude" / "hooks.json"
    assert not hooks_file.exists(), "Hook was incorrectly written to .claude/hooks.json"


def test_setup_hook_format_matches_claude_code(tmp_path):
    """Verify the hook JSON structure matches what Claude Code expects.

    Claude Code expects:
    {
      "hooks": {
        "Stop": [
          {"hooks": [{"type": "command", "command": "/path/to/script.sh"}]}
        ]
      }
    }
    """
    os.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()

    from auto.cli import _setup_hook
    _setup_hook()

    settings_file = tmp_path / ".claude" / "settings.local.json"
    with open(settings_file) as f:
        config = json.load(f)

    # Structure check
    assert "hooks" in config, "Missing 'hooks' key"
    assert "Stop" in config["hooks"], "Missing 'Stop' key in hooks"

    stop_hooks = config["hooks"]["Stop"]
    assert isinstance(stop_hooks, list), "Stop hooks must be a list"
    assert len(stop_hooks) >= 1, "Must have at least one Stop hook group"

    group = stop_hooks[0]
    assert "hooks" in group, "Hook group must have 'hooks' key"
    assert len(group["hooks"]) >= 1, "Hook group must have at least one hook"

    hook = group["hooks"][0]
    assert hook["type"] == "command", f"Hook type must be 'command', got {hook['type']}"
    assert "stop-hook.sh" in hook["command"], "Command must point to stop-hook.sh"
    assert os.path.isabs(hook["command"]), "Command path must be absolute"


def test_setup_hook_script_exists_and_executable(tmp_path):
    """The hook script must exist and be executable."""
    hook_script = Path(__file__).parent.parent / "src" / "auto" / "hooks" / "stop-hook.sh"
    assert hook_script.exists(), f"stop-hook.sh not found at {hook_script}"
    assert os.access(hook_script, os.X_OK), f"stop-hook.sh is not executable"


def test_setup_idempotent(tmp_path):
    """Running setup twice should not duplicate the hook entry."""
    os.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()

    from auto.cli import _setup_hook
    _setup_hook()
    _setup_hook()  # second call

    settings_file = tmp_path / ".claude" / "settings.local.json"
    with open(settings_file) as f:
        config = json.load(f)

    stop_hooks = config["hooks"]["Stop"]
    assert len(stop_hooks) == 1, f"Expected 1 hook group, got {len(stop_hooks)}"


def test_setup_preserves_existing_settings(tmp_path):
    """Setup should not clobber other settings in settings.local.json."""
    os.chdir(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # Pre-existing settings
    existing = {"permissions": {"allow": ["Bash(ls)"]}, "other_key": True}
    with open(claude_dir / "settings.local.json", "w") as f:
        json.dump(existing, f)

    from auto.cli import _setup_hook
    _setup_hook()

    with open(claude_dir / "settings.local.json") as f:
        config = json.load(f)

    assert config["permissions"] == {"allow": ["Bash(ls)"]}, "Existing permissions were clobbered"
    assert config["other_key"] is True, "Existing keys were clobbered"
    assert "hooks" in config, "Hooks were not added"


# --- Start program verification ---

def test_start_program_checks_settings_not_hooks_json(tmp_path):
    """_start_program should check settings.local.json, not hooks.json."""
    os.chdir(tmp_path)

    # Create hooks.json (wrong file) — should NOT satisfy the check
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    with open(claude_dir / "hooks.json", "w") as f:
        json.dump({"hooks": {"Stop": []}}, f)

    from auto.cli import _start_program

    # Should fail because settings.local.json doesn't exist
    with pytest.raises(SystemExit):
        _start_program(str(tmp_path / "fake_program.py"))


# --- Hook script basic tests ---

def test_hook_script_exits_clean_without_state_file(tmp_path):
    """Hook should exit 0 when no state file exists."""
    os.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()

    hook_script = Path(__file__).parent.parent / "src" / "auto" / "hooks" / "stop-hook.sh"
    result = subprocess.run(
        ["bash", str(hook_script)],
        input='{"session_id": "", "transcript_path": "/tmp/none.jsonl", "hook_event_name": "Stop"}',
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, f"Hook failed: {result.stderr}"
    assert result.stdout.strip() == "", "Hook should produce no output without state file"


def test_hook_script_picks_up_pending(tmp_path):
    """Hook should read pending instruction and output block decision."""
    os.chdir(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # Write pending state
    state = {
        "status": "pending",
        "session_id": "",
        "step_number": 1,
        "instruction": "say hello",
        "schema": None,
        "response": None,
        "error": None,
        "python_pid": os.getpid(),  # use our own PID (alive)
        "cwd": str(tmp_path),
    }
    with open(claude_dir / "auto-loop.json", "w") as f:
        json.dump(state, f)

    hook_script = Path(__file__).parent.parent / "src" / "auto" / "hooks" / "stop-hook.sh"
    result = subprocess.run(
        ["bash", str(hook_script)],
        input='{"session_id": "", "transcript_path": "/tmp/none.jsonl", "hook_event_name": "Stop"}',
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, f"Hook failed: {result.stderr}"

    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert "say hello" in output["reason"]

    # State should now be "running"
    with open(claude_dir / "auto-loop.json") as f:
        updated = json.load(f)
    assert updated["status"] == "running"


def test_hook_script_schema_augments_prompt(tmp_path):
    """When schema is set, the hook should append JSON formatting instructions."""
    os.chdir(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    state = {
        "status": "pending",
        "session_id": "",
        "step_number": 1,
        "instruction": "report score",
        "schema": {"score": "float"},
        "response": None,
        "error": None,
        "python_pid": os.getpid(),
        "cwd": str(tmp_path),
    }
    with open(claude_dir / "auto-loop.json", "w") as f:
        json.dump(state, f)

    hook_script = Path(__file__).parent.parent / "src" / "auto" / "hooks" / "stop-hook.sh"
    result = subprocess.run(
        ["bash", str(hook_script)],
        input='{"session_id": "", "transcript_path": "/tmp/none.jsonl", "hook_event_name": "Stop"}',
        capture_output=True, text=True, cwd=tmp_path,
    )
    output = json.loads(result.stdout)
    assert "Respond with ONLY a JSON object" in output["reason"]
    assert "score" in output["reason"]


def test_hook_script_dead_pid_cleanup(tmp_path):
    """Hook should clean up state file when Python PID is dead."""
    os.chdir(tmp_path)
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    state = {
        "status": "pending",
        "session_id": "",
        "step_number": 1,
        "instruction": "test",
        "schema": None,
        "response": None,
        "error": None,
        "python_pid": 99999,  # dead PID
        "cwd": str(tmp_path),
    }
    with open(claude_dir / "auto-loop.json", "w") as f:
        json.dump(state, f)

    hook_script = Path(__file__).parent.parent / "src" / "auto" / "hooks" / "stop-hook.sh"
    result = subprocess.run(
        ["bash", str(hook_script)],
        input='{"session_id": "", "transcript_path": "/tmp/none.jsonl", "hook_event_name": "Stop"}',
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0
    assert not (claude_dir / "auto-loop.json").exists(), "State file should be deleted for dead PID"


def test_hook_transcript_tail_jq_returns_last_assistant_text(tmp_path):
    """Transcript extraction uses tail -n 200 | jq -rs with role filtering in jq.

    Tests the actual pipeline in stop-hook.sh Phase 1 directly, without running
    the full hook loop (which would block in Phase 2).
    """
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_lines = [
        '{"type": "message", "role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}',
        '{"type": "message", "role": "assistant", "message": {"content": [{"type": "text", "text": "hello from assistant"}]}}',
    ]
    transcript_path.write_text("\n".join(transcript_lines) + "\n")

    # Run the exact pipeline used in stop-hook.sh lines 59-61
    result = subprocess.run(
        [
            "bash", "-c",
            f"tail -n 200 {transcript_path}"
            f" | jq -rs '[.[] | select(.role == \"assistant\") | .message.content[]? | select(.type == \"text\") | .text] | join(\"\\n\")'"
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"tail/jq failed: {result.stderr}"
    output = result.stdout.strip()
    assert output == "hello from assistant", f"Got: {output!r}"


def test_hook_only_extracts_current_turn_text(tmp_path):
    """Hook Phase 1 must only extract text from the CURRENT turn, not prior turns.

    Simulates a transcript with two turns. transcript_lines points past turn 1.
    Only turn 2's text should appear in the extraction result.
    """
    transcript_path = tmp_path / "transcript.jsonl"
    # Turn 1 lines (lines 1–2): old assistant text with stale JSON
    turn1_lines = [
        '{"type": "message", "role": "user", "message": {"content": [{"type": "text", "text": "q1"}]}}',
        '{"type": "message", "role": "assistant", "message": {"content": [{"type": "text", "text": "{\\"old\\": true}"}]}}',
    ]
    # Turn 2 lines (lines 3–4): new assistant text with fresh JSON
    turn2_lines = [
        '{"type": "message", "role": "user", "message": {"content": [{"type": "text", "text": "q2"}]}}',
        '{"type": "message", "role": "assistant", "message": {"content": [{"type": "text", "text": "{\\"new\\": true}"}]}}',
    ]
    transcript_path.write_text("\n".join(turn1_lines + turn2_lines) + "\n")

    # PREV_LINES is 2: the hook recorded the line count after turn 1 was written
    prev_lines = len(turn1_lines)

    result = subprocess.run(
        [
            "bash", "-c",
            f"tail -n +{prev_lines + 1} {transcript_path}"
            f" | jq -rs '[.[] | select(.role == \"assistant\") | .message.content[]? | select(.type == \"text\") | .text] | join(\"\\n\")'"
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"tail/jq failed: {result.stderr}"
    output = result.stdout.strip()
    assert '{"new": true}' in output, f"Expected new-turn JSON in output, got: {output!r}"
    assert '{"old": true}' not in output, f"Old-turn JSON must NOT appear in output, got: {output!r}"


def test_hook_transcript_tail_jq_returns_all_assistant_text_joined(tmp_path):
    """When multiple assistant turns exist, jq join returns all text concatenated."""
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_lines = [
        '{"type": "message", "role": "user", "message": {"content": [{"type": "text", "text": "q1"}]}}',
        '{"type": "message", "role": "assistant", "message": {"content": [{"type": "text", "text": "answer one"}]}}',
        '{"type": "message", "role": "user", "message": {"content": [{"type": "text", "text": "q2"}]}}',
        '{"type": "message", "role": "assistant", "message": {"content": [{"type": "text", "text": "answer two"}]}}',
    ]
    transcript_path.write_text("\n".join(transcript_lines) + "\n")

    result = subprocess.run(
        [
            "bash", "-c",
            f"tail -n 200 {transcript_path}"
            f" | jq -rs '[.[] | select(.role == \"assistant\") | .message.content[]? | select(.type == \"text\") | .text] | join(\"\\n\")'"
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"tail/jq failed: {result.stderr}"
    output = result.stdout.strip()
    assert "answer one" in output and "answer two" in output, f"Got: {output!r}"
