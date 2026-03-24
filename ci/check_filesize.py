#!/usr/bin/env python3
"""Enforce a 500-line maximum for Python files under loom/."""

import pathlib
import sys

MAX_LINES = 500


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    loom_dir = root / "src" / "loom"
    violations = []

    for py_file in sorted(loom_dir.rglob("*.py")):
        line_count = len(py_file.read_text().splitlines())
        status = "OK" if line_count <= MAX_LINES else "OVER"
        print(f"  {status:4s} {py_file.relative_to(root)} ({line_count} lines)")
        if line_count > MAX_LINES:
            violations.append((py_file.relative_to(root), line_count))

    if violations:
        print(f"\n{len(violations)} file(s) exceed {MAX_LINES}-line limit.")
        sys.exit(1)
    else:
        print(f"\nAll files within {MAX_LINES}-line limit.")


if __name__ == "__main__":
    main()
