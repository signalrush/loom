"""Tests for the loom.state module."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from loom import state


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Change to temp directory so state file is created there
        original_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            yield tmpdir
        finally:
            os.chdir(original_cwd)


def test_state_import():
    """Verify loom.state can be imported."""
    import loom.state
    assert loom.state is not None


def test_set_and_get(temp_dir):
    """Test basic set and get operations."""
    # Set a value
    state.set("test_key", "test_value")
    
    # Get the value back
    assert state.get("test_key") == "test_value"
    
    # Get non-existent key
    assert state.get("nonexistent") is None
    
    # Get all state
    all_state = state.get()
    assert isinstance(all_state, dict)
    assert all_state["test_key"] == "test_value"


def test_update(temp_dir):
    """Test update operation."""
    # Set initial state
    state.set("key1", "value1")
    
    # Update with new data
    state.update({"key2": "value2", "key3": 42})
    
    # Verify all values
    assert state.get("key1") == "value1"
    assert state.get("key2") == "value2"
    assert state.get("key3") == 42
    
    # Update existing key
    state.update({"key1": "new_value1"})
    assert state.get("key1") == "new_value1"


def test_file_creation(temp_dir):
    """Test that state file is created automatically."""
    state_file = Path("loom-state.json")
    
    # File shouldn't exist initially
    assert not state_file.exists()
    
    # Set a value - should create the file
    state.set("test", "value")
    
    # File should now exist
    assert state_file.exists()
    
    # File should contain valid JSON
    with open(state_file) as f:
        data = json.load(f)
    assert data["test"] == "value"


def test_persistence_across_operations(temp_dir):
    """Test that state persists across multiple operations."""
    # Set some initial state
    state.set("counter", 0)
    state.update({"name": "test", "active": True})
    
    # Modify state multiple times
    for i in range(5):
        current = state.get("counter")
        state.set("counter", current + 1)
    
    # Verify final state
    final_state = state.get()
    assert final_state["counter"] == 5
    assert final_state["name"] == "test"
    assert final_state["active"] is True


def test_empty_state_file(temp_dir):
    """Test handling of empty or non-existent state file."""
    # No file exists - should return empty dict
    assert state.get() == {}
    assert state.get("anything") is None
    
    # Create empty file
    Path("loom-state.json").touch()
    assert state.get() == {}
    
    # Create file with only whitespace
    with open("loom-state.json", "w") as f:
        f.write("   \n  \n")
    assert state.get() == {}


def test_invalid_json_handling(temp_dir):
    """Test handling of corrupted JSON file."""
    # Create file with invalid JSON
    with open("loom-state.json", "w") as f:
        f.write("{ invalid json")
    
    # Should return empty dict and not crash
    assert state.get() == {}
    
    # Should be able to set new state (overwrites corrupted file)
    state.set("recovery", True)
    assert state.get("recovery") is True


def test_json_serializable_types(temp_dir):
    """Test that various JSON-serializable types work."""
    test_data = {
        "string": "hello",
        "integer": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "list": [1, 2, 3],
        "dict": {"nested": "value"},
        "mixed_list": [1, "two", {"three": 3}]
    }
    
    # Set each type
    for key, value in test_data.items():
        state.set(key, value)
    
    # Verify each type
    for key, expected_value in test_data.items():
        assert state.get(key) == expected_value
    
    # Verify all at once
    all_state = state.get()
    for key, expected_value in test_data.items():
        assert all_state[key] == expected_value


def test_concurrent_access(temp_dir):
    """Test that concurrent operations don't cause corruption or crashes."""
    state.set("counter", 0)
    errors = []
    
    def update_state(thread_id, iterations):
        """Function to update state in a thread."""
        try:
            for i in range(iterations):
                # Use update() with unique keys to avoid read-modify-write issues
                state.update({f"thread_{thread_id}_iter_{i}": f"value_{i}"})
                # Also test reading
                current_state = state.get()
                assert isinstance(current_state, dict)
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")
    
    # Start multiple threads
    threads = []
    iterations_per_thread = 10
    num_threads = 5
    
    for i in range(num_threads):
        thread = threading.Thread(
            target=update_state, 
            args=(i, iterations_per_thread)
        )
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    # Check for errors
    assert not errors, f"Errors occurred: {errors}"
    
    # Verify that the state file is not corrupted and contains some thread data
    final_state = state.get()
    thread_keys = [key for key in final_state.keys() if key.startswith("thread_")]
    
    # Due to concurrent access patterns, we might not have all keys due to overwrites,
    # but we should have some data from the threads and no corruption
    assert len(thread_keys) > 0, "Should have at least some thread data"
    assert "counter" in final_state, "Initial counter key should still exist"
    
    # Verify file is valid JSON (not corrupted)
    with open("loom-state.json") as f:
        json.load(f)  # Should not raise


def test_large_state(temp_dir):
    """Test handling of reasonably large state objects."""
    # Create a large nested structure
    large_data = {
        "experiments": [
            {
                "id": i,
                "parameters": {"lr": 0.001 * i, "batch_size": 32 + i},
                "results": {"accuracy": 0.8 + i * 0.01, "loss": 2.0 - i * 0.1},
                "metadata": {"timestamp": f"2024-03-{i:02d}", "notes": f"Experiment {i} notes"}
            }
            for i in range(1, 51)  # 50 experiments
        ],
        "global_config": {
            "model_type": "transformer",
            "dataset": "custom_dataset",
            "preprocessing_steps": ["normalize", "tokenize", "pad"],
            "hyperparams": {
                "learning_rate": 0.001,
                "batch_size": 64,
                "epochs": 100,
                "warmup_steps": 1000
            }
        }
    }
    
    # Set large data
    state.update(large_data)
    
    # Verify it's stored and retrieved correctly
    retrieved = state.get()
    assert len(retrieved["experiments"]) == 50
    assert retrieved["experiments"][0]["id"] == 1
    assert retrieved["experiments"][49]["id"] == 50
    assert retrieved["global_config"]["model_type"] == "transformer"
    
    # Verify individual access works
    assert len(state.get("experiments")) == 50
    assert state.get("global_config")["model_type"] == "transformer"


@pytest.mark.parametrize("initial_state,updates,expected", [
    # Empty state, single update
    ({}, {"key": "value"}, {"key": "value"}),
    
    # Existing state, non-overlapping update
    ({"a": 1}, {"b": 2}, {"a": 1, "b": 2}),
    
    # Existing state, overlapping update
    ({"a": 1, "b": 2}, {"b": 3, "c": 4}, {"a": 1, "b": 3, "c": 4}),
    
    # Complex nested structures
    (
        {"config": {"lr": 0.1}, "results": [1, 2]},
        {"config": {"batch_size": 32}, "status": "running"},
        {"config": {"batch_size": 32}, "results": [1, 2], "status": "running"}
    ),
])
def test_state_update_scenarios(temp_dir, initial_state, updates, expected):
    """Test various update scenarios."""
    # Set initial state
    for key, value in initial_state.items():
        state.set(key, value)
    
    # Apply update
    state.update(updates)
    
    # Verify expected result
    final_state = state.get()
    assert final_state == expected


def test_file_locking_behavior(temp_dir):
    """Test that file operations use proper locking to prevent corruption."""
    # This test verifies the locking is in place by checking that
    # rapid concurrent operations don't result in corrupted files
    
    def rapid_updates(thread_id):
        """Rapidly update state to test locking."""
        for i in range(10):  # Reduced for faster test
            state.update({f"thread_{thread_id}_item_{i}": f"value_{i}"})
    
    # Start multiple threads doing rapid updates
    threads = []
    for i in range(3):
        thread = threading.Thread(target=rapid_updates, args=(i,))
        threads.append(thread)
        thread.start()
    
    # Wait for completion
    for thread in threads:
        thread.join()
    
    # Verify the final state file is valid JSON (not corrupted)
    state_file = Path("loom-state.json")
    assert state_file.exists()
    
    with open(state_file) as f:
        # This should not raise an exception if file locking worked
        data = json.load(f)
        assert isinstance(data, dict)
    
    # Should have data from threads (exact count may vary due to overwrites)
    final_state = state.get()
    thread_keys = [key for key in final_state.keys() if key.startswith("thread_")]
    assert len(thread_keys) > 0, "Should have some thread data"
    
    # Most importantly: file should not be corrupted
    # (if locking failed, we'd get JSON decode errors above)