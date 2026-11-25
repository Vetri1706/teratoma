#!/usr/bin/env python3
"""Run tests that do not require POSIX storage or compiled native queues.

The Linux CI job runs the complete suite after building the native components.
This runner intentionally leaves those checks there instead of treating missing
native dependencies as successful tests on Windows.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NATIVE_MODULES = {
    "tests.test_native_queue",  # C shared library, pthread stress, ELF/Mach-O daemon.
    "tests.test_persistence",  # POSIX flock and durable directory fsync.
}
NATIVE_TESTS = {
    "tests.test_transport_fragments.TransportFragmentTests."
    "test_real_worker_fragmented_reply_and_credit_drain",
}


def portable_cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from portable_cases(case)
        elif case.id() not in NATIVE_TESTS:
            yield case


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = "tests." + path.stem
        if module not in NATIVE_MODULES:
            suite.addTests(portable_cases(loader.loadTestsFromName(module)))
    print("Portable suite: POSIX persistence and native-queue checks run separately on Linux.",
          flush=True)
    if not suite.countTestCases():
        raise SystemExit("No portable tests were discovered")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
