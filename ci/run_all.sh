#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

FAIL=0

echo "=== Import smoke test ==="
if python3 ci/check_imports.py; then
    echo "PASS"
else
    echo "FAIL"
    FAIL=1
fi

echo ""
echo "=== File size check ==="
if python3 ci/check_filesize.py; then
    echo "PASS"
else
    echo "FAIL"
    FAIL=1
fi

echo ""
echo "=== Test coverage check ==="
if python3 ci/check_test_coverage.py; then
    echo "PASS"
else
    echo "FAIL"
    FAIL=1
fi

echo ""
echo "=== Pytest ==="
if python3 -m pytest tests/ -v; then
    echo "PASS"
else
    echo "FAIL"
    FAIL=1
fi

echo ""
if [ "$FAIL" -ne 0 ]; then
    echo "Some checks FAILED."
    exit 1
else
    echo "All checks PASSED."
fi
