#!/usr/bin/env python3
"""Ensure every source module under loom/ (except __init__.py) has a test file."""

import pathlib
import sys


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    loom_dir = root / "loom"
    tests_dir = root / "tests"
    missing = []

    for py_file in sorted(loom_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        module_name = py_file.stem
        test_file = tests_dir / f"test_{module_name}.py"
        if test_file.exists():
            print(f"  OK  {py_file.relative_to(root)} -> {test_file.relative_to(root)}")
        else:
            print(f"  MISS {py_file.relative_to(root)} -> {test_file.relative_to(root)}")
            missing.append((py_file.relative_to(root), test_file.relative_to(root)))

    if missing:
        print(f"\n{len(missing)} module(s) missing test files:")
        for src, tst in missing:
            print(f"  {src} -> {tst}")
        sys.exit(1)
    else:
        print("\nAll modules have test files.")


if __name__ == "__main__":
    main()
