"""State helper module for loom programs.

A simple module that writes/reads structured progress to loom-state.json
in the current working directory.

Usage:
    from loom import state
    
    state.set("status", "running")
    state.update({"best_loss": 0.23, "step": 7})
    val = state.get("best_loss")  # returns 0.23
    all_state = state.get()       # returns full dict
"""

import json
import os
import fcntl
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Union


STATE_FILE = "loom-state.json"


def _get_state_file() -> Path:
    """Get the path to the state file in the current working directory."""
    return Path.cwd() / STATE_FILE


def _load_state() -> Dict[str, Any]:
    """Load state from file with file locking. Returns empty dict if file doesn't exist."""
    state_file = _get_state_file()
    
    if not state_file.exists():
        return {}
    
    try:
        with open(state_file, 'r') as f:
            # Acquire shared lock for reading
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(data: Dict[str, Any]) -> None:
    """Save state to file using atomic write with file locking."""
    state_file = _get_state_file()
    
    # Create a temporary file in the same directory for atomic write
    temp_fd = None
    temp_path = None
    
    try:
        # Create temporary file in same directory as target
        temp_fd, temp_path = tempfile.mkstemp(
            dir=state_file.parent,
            prefix='.loom-state-',
            suffix='.tmp'
        )
        
        with os.fdopen(temp_fd, 'w') as temp_file:
            temp_fd = None  # File object now owns the file descriptor
            
            # Acquire exclusive lock for writing
            fcntl.flock(temp_file.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, temp_file, indent=2)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            finally:
                fcntl.flock(temp_file.fileno(), fcntl.LOCK_UN)
        
        # Atomically replace the original file
        os.rename(temp_path, state_file)
        temp_path = None  # Successfully moved, don't clean up
        
    finally:
        # Clean up on error
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def set(key: str, value: Any) -> None:
    """Set a single key-value pair in the state.
    
    Args:
        key: The state key
        value: The value to set (must be JSON serializable)
    """
    current_state = _load_state()
    current_state[key] = value
    _save_state(current_state)


def update(data: Dict[str, Any]) -> None:
    """Merge a dictionary into the current state.
    
    Args:
        data: Dictionary to merge into state (must be JSON serializable)
    """
    current_state = _load_state()
    current_state.update(data)
    _save_state(current_state)


def get(key: Optional[str] = None) -> Any:
    """Get a value from state or the entire state dict.
    
    Args:
        key: The key to get. If None, returns the entire state dict.
        
    Returns:
        The value for the key, the entire state dict if key is None,
        or None if key doesn't exist.
    """
    state = _load_state()
    if key is None:
        return state
    return state.get(key)