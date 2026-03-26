"""E2E tests for CLI logging, PID file, and symlink management.

Covers edge cases in _start_program, _show_status, _tail_log, _stop_program
around log file creation, symlink handling, and PID lifecycle.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

import auto.cli as cli_mod


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Set up a tmp_path-based environment and monkeypatch module-level constants."""
    log_dir = str(tmp_path / ".claude" / "logs")
    log_link = os.path.join(log_dir, "auto.log")
    pid_file = str(tmp_path / ".claude" / "auto.pid")

    monkeypatch.setattr(cli_mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(cli_mod, "LOG_LINK", log_link)
    monkeypatch.setattr(cli_mod, "PID_FILE", pid_file)

    # Create .claude dir so _setup_hook can write settings
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)

    # Create a fake program file
    prog = tmp_path / "prog.py"
    prog.write_text("def main(): pass\n")

    # Create state file so _start_program doesn't wait 3s in polling loop
    (tmp_path / ".claude" / "auto-loop.json").write_text("{}")

    monkeypatch.chdir(tmp_path)

    return {
        "tmp_path": tmp_path,
        "log_dir": log_dir,
        "log_link": log_link,
        "pid_file": pid_file,
        "prog": str(prog),
    }


def _mock_popen(pid=12345):
    """Create a mock Popen that behaves enough for _start_program."""
    mock = MagicMock()
    mock.pid = pid
    return mock


# ---------------------------------------------------------------------------
# Per-run log file naming
# ---------------------------------------------------------------------------

class TestLogFileCreation:

    def test_log_file_created_with_correct_pattern(self, cli_env):
        """Per-run log files should be named auto-YYYYMMDD-HHMMSS-<pid>.log."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen()):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        log_dir = Path(cli_env["log_dir"])
        log_files = list(log_dir.glob("auto-*.log"))
        assert len(log_files) == 1, f"Expected 1 log file, got {log_files}"

        name = log_files[0].name
        # Pattern: auto-YYYYMMDD-HHMMSS-PID.log
        parts = name.replace("auto-", "").replace(".log", "").split("-")
        assert len(parts) == 3, f"Unexpected log file name format: {name}"
        date_part, time_part, pid_part = parts
        assert len(date_part) == 8, f"Date part should be 8 chars: {date_part}"
        assert len(time_part) == 6, f"Time part should be 6 chars: {time_part}"
        assert pid_part.isdigit(), f"PID part should be numeric: {pid_part}"

    def test_multiple_runs_create_separate_log_files(self, cli_env):
        """Each run should create a distinct log file, not overwrite previous ones."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen(111)):
            with patch.object(cli_mod, "_setup_hook"):
                # First run -- must not see an existing PID file
                cli_mod._start_program(cli_env["prog"])

        # Remove PID file to allow second run
        os.remove(cli_env["pid_file"])

        # Ensure different timestamp by patching time.strftime
        with patch("auto.cli.time") as mock_time:
            mock_time.strftime.return_value = "20260327-000001"
            mock_time.time.return_value = time.time() + 10
            with patch("auto.cli.os.getpid", return_value=99999):
                with patch.object(subprocess, "Popen", return_value=_mock_popen(222)):
                    with patch.object(cli_mod, "_setup_hook"):
                        cli_mod._start_program(cli_env["prog"])

        log_dir = Path(cli_env["log_dir"])
        log_files = sorted(log_dir.glob("auto-*.log"))
        # Filter out .lnk temp files
        log_files = [f for f in log_files if not f.name.endswith(".lnk")]
        assert len(log_files) == 2, f"Expected 2 log files, got {[f.name for f in log_files]}"


# ---------------------------------------------------------------------------
# Symlink behavior
# ---------------------------------------------------------------------------

class TestSymlink:

    def test_symlink_points_to_latest_log(self, cli_env):
        """auto.log symlink should point to the most recent run's log file."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen()):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        link = cli_env["log_link"]
        assert os.path.islink(link), "auto.log should be a symlink"
        target = os.readlink(link)
        assert not os.path.isabs(target), (
            f"Symlink should be relative, got absolute path: {target}"
        )
        assert target.startswith("auto-"), f"Symlink target should be a log file: {target}"

    def test_symlink_is_relative_not_absolute(self, cli_env):
        """Symlink must use a relative path (basename only) so the project is portable."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen()):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        link = cli_env["log_link"]
        target = os.readlink(link)
        assert "/" not in target, (
            f"Relative symlink should not contain '/': {target}"
        )

    def test_atomic_symlink_replacement_on_second_run(self, cli_env):
        """Second run should atomically replace the symlink to point to new log."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen(111)):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        first_target = os.readlink(cli_env["log_link"])

        # Remove PID file to allow second run
        os.remove(cli_env["pid_file"])

        with patch("auto.cli.time") as mock_time:
            mock_time.strftime.return_value = "20260327-235959"
            mock_time.time.return_value = time.time() + 10
            with patch("auto.cli.os.getpid", return_value=77777):
                with patch.object(subprocess, "Popen", return_value=_mock_popen(222)):
                    with patch.object(cli_mod, "_setup_hook"):
                        cli_mod._start_program(cli_env["prog"])

        second_target = os.readlink(cli_env["log_link"])
        assert first_target != second_target, (
            "Symlink should point to a different log after second run"
        )
        assert second_target.startswith("auto-"), f"Unexpected target: {second_target}"

    def test_symlink_fallback_when_rename_fails(self, cli_env):
        """If os.rename fails (e.g. cross-device), fallback should still create symlink."""
        def fake_rename(src, dst):
            raise OSError("cross-device link")

        with patch("auto.cli.os.rename", side_effect=fake_rename):
            with patch.object(subprocess, "Popen", return_value=_mock_popen()):
                with patch.object(cli_mod, "_setup_hook"):
                    cli_mod._start_program(cli_env["prog"])

        link = cli_env["log_link"]
        assert os.path.islink(link), "Symlink should exist even after rename failure"
        target = os.readlink(link)
        assert not os.path.isabs(target), "Fallback symlink should still be relative"


# ---------------------------------------------------------------------------
# Dangling symlink handling
# ---------------------------------------------------------------------------

class TestDanglingSymlink:

    def test_tail_log_with_dangling_symlink(self, cli_env, capsys):
        """_tail_log should detect dangling symlink and exit with error message."""
        log_dir = Path(cli_env["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        # Create a symlink pointing to a non-existent file
        os.symlink("auto-nonexistent.log", cli_env["log_link"])

        with pytest.raises(SystemExit) as exc_info:
            cli_mod._tail_log()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "target is missing" in captured.err

    def test_tail_log_no_symlink_at_all(self, cli_env, capsys):
        """_tail_log should report no log file when symlink doesn't exist."""
        Path(cli_env["log_dir"]).mkdir(parents=True, exist_ok=True)

        with pytest.raises(SystemExit) as exc_info:
            cli_mod._tail_log()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "No log file found" in captured.err

    def test_show_status_with_dangling_symlink(self, cli_env, capsys):
        """_show_status should detect dangling symlink and report it gracefully."""
        log_dir = Path(cli_env["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        os.symlink("auto-deleted-run.log", cli_env["log_link"])

        cli_mod._show_status()

        captured = capsys.readouterr()
        assert "target is missing" in captured.out

    def test_show_status_with_valid_symlink(self, cli_env, capsys):
        """_show_status should read and display last 10 lines from valid log."""
        log_dir = Path(cli_env["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create actual log file with content
        log_file = log_dir / "auto-20260326-120000-1234.log"
        lines = [f"line {i}\n" for i in range(15)]
        log_file.write_text("".join(lines))
        os.symlink(log_file.name, cli_env["log_link"])

        cli_mod._show_status()

        captured = capsys.readouterr()
        # Should show last 10 lines (line 5 through line 14)
        assert "line 5" in captured.out
        assert "line 14" in captured.out
        # Should NOT show line 0-4
        assert "line 4\n" not in captured.out

    def test_show_status_no_log_at_all(self, cli_env, capsys):
        """_show_status should report 'No log file found' when nothing exists."""
        Path(cli_env["log_dir"]).mkdir(parents=True, exist_ok=True)

        cli_mod._show_status()

        captured = capsys.readouterr()
        assert "No log file found" in captured.out


# ---------------------------------------------------------------------------
# PID file
# ---------------------------------------------------------------------------

class TestPidFile:

    def test_pid_file_created_on_start(self, cli_env):
        """PID file should be written with the child process PID."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen(42)):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        pid_file = Path(cli_env["pid_file"])
        assert pid_file.exists(), "PID file should be created"
        assert pid_file.read_text().strip() == "42"

    def test_start_refuses_if_pid_still_running(self, cli_env):
        """_start_program should refuse to start if PID file references a live process."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        # Use our own PID -- guaranteed to be alive
        pid_file.write_text(str(os.getpid()))

        with pytest.raises(SystemExit):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

    def test_start_cleans_stale_pid(self, cli_env):
        """_start_program should remove PID file if the old process is dead."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("99999999")  # almost certainly not running

        with patch.object(subprocess, "Popen", return_value=_mock_popen(555)):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        assert pid_file.read_text().strip() == "555"

    def test_start_corrupted_pid_file_cleans_up(self, cli_env):
        """_start_program should handle corrupted PID file gracefully by removing it."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("not-a-number")

        with patch.object(subprocess, "Popen", return_value=_mock_popen(999)):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        # Should have cleaned up corrupted PID and written new one
        assert pid_file.read_text() == "999"


# ---------------------------------------------------------------------------
# _stop_program
# ---------------------------------------------------------------------------

class TestStopProgram:

    def test_stop_uses_missing_ok_for_cleanup(self, cli_env, capsys):
        """_stop_program should not crash if PID file disappears during cleanup."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        # Simulate: process lookup says it's dead (already stopped)
        with patch("auto.cli.os.killpg", side_effect=ProcessLookupError):
            cli_mod._stop_program()

        assert not pid_file.exists(), "PID file should be cleaned up"
        captured = capsys.readouterr()
        assert "already stopped" in captured.out.lower()

    def test_stop_no_pid_file(self, cli_env, capsys):
        """_stop_program should report no program running when no PID file exists."""
        cli_mod._stop_program()
        captured = capsys.readouterr()
        assert "No running auto program found" in captured.out

    def test_stop_corrupted_pid_file_cleaned(self, cli_env, capsys):
        """_stop_program should handle and remove corrupted PID files."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("garbage-data")

        cli_mod._stop_program()

        assert not pid_file.exists(), "Corrupted PID file should be removed"
        captured = capsys.readouterr()
        assert "corrupted" in captured.err.lower()

    def test_stop_corrupted_pid_cleans_up_safely(self, cli_env, capsys):
        """_stop_program should handle corrupted PID file without crashing,
        even if the file disappears between read and unlink (TOCTOU race)."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("garbage")

        # Should not raise — uses Path.unlink(missing_ok=True)
        cli_mod._stop_program()
        captured = capsys.readouterr()
        assert "corrupted" in captured.err.lower()
        assert not pid_file.exists()


# ---------------------------------------------------------------------------
# Log file handle cleanup
# ---------------------------------------------------------------------------

class TestLogFileHandleCleanup:

    def test_log_fh_closed_on_popen_failure(self, cli_env):
        """Log file handle must be closed even when Popen raises."""
        with patch.object(subprocess, "Popen", side_effect=OSError("exec failed")):
            with patch.object(cli_mod, "_setup_hook"):
                with pytest.raises(OSError, match="exec failed"):
                    cli_mod._start_program(cli_env["prog"])

        # Verify the log file was created (open succeeded) but handle is closed.
        # We can't directly check the fd, but we can verify the file exists
        # and is not locked (can be opened and read).
        log_dir = Path(cli_env["log_dir"])
        log_files = list(log_dir.glob("auto-*.log"))
        assert len(log_files) == 1, "Log file should be created even on Popen failure"
        # Should be readable (fd was closed)
        content = log_files[0].read_text()
        assert content == "", "Log file should be empty (nothing was written)"

    def test_log_fh_closed_on_popen_success(self, cli_env):
        """Log file handle should be closed in the parent after successful Popen."""
        with patch.object(subprocess, "Popen", return_value=_mock_popen()):
            with patch.object(cli_mod, "_setup_hook"):
                cli_mod._start_program(cli_env["prog"])

        # Verify we can read the file (parent closed its handle)
        log_dir = Path(cli_env["log_dir"])
        log_files = list(log_dir.glob("auto-*.log"))
        assert len(log_files) == 1
        log_files[0].read_text()  # should not raise


# ---------------------------------------------------------------------------
# _show_status PID handling
# ---------------------------------------------------------------------------

class TestShowStatusPid:

    def test_show_status_running_process(self, cli_env, capsys):
        """_show_status should report 'Running' for a live PID."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        cli_mod._show_status()

        captured = capsys.readouterr()
        assert "Running" in captured.out
        assert str(os.getpid()) in captured.out

    def test_show_status_stale_pid_cleaned(self, cli_env, capsys):
        """_show_status should clean up stale PID file and report 'Not running'."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("99999999")

        cli_mod._show_status()

        captured = capsys.readouterr()
        assert "stale" in captured.out.lower()
        assert not pid_file.exists(), "Stale PID file should be removed"

    def test_show_status_corrupted_pid(self, cli_env, capsys):
        """_show_status should handle corrupted PID file gracefully."""
        pid_file = Path(cli_env["pid_file"])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("not-a-pid")

        cli_mod._show_status()

        captured = capsys.readouterr()
        assert "corrupted" in captured.out.lower()
