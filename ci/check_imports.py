#!/usr/bin/env python3
"""Smoke-import every Python module under loom/. Fail if any import errors."""

import importlib
import pathlib
import sys


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "src"))
    loom_dir = root / "src" / "loom"
    errors = []

    for py_file in sorted(loom_dir.rglob("*.py")):
        rel = py_file.relative_to(root / "src")
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module_name = ".".join(parts)
        try:
            importlib.import_module(module_name)
            print(f"  OK  {module_name}")
        except Exception as exc:
            print(f"  FAIL {module_name}: {exc}")
            errors.append((module_name, exc))

    if errors:
        print(f"\n{len(errors)} import(s) failed.")
        sys.exit(1)
    else:
        print("\nAll imports OK.")


if __name__ == "__main__":
    main()
