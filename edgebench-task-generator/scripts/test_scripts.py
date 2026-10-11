#!/usr/bin/env python3
"""Run the independent EdgeBench task-generator test suite."""

from pathlib import Path
import sys
import unittest


def main() -> int:
    tests = Path(__file__).resolve().parents[1] / "tests"
    suite = unittest.TestLoader().discover(str(tests), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
