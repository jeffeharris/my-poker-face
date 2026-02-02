#!/usr/bin/env python3
"""
Test runner utility for running tests inside Docker containers.

IMPORTANT: All tests run inside Docker to ensure correct dependencies.

Usage:
    # From command line:
    python scripts/test.py                     # Run all Python tests
    python scripts/test.py --quick             # Run fast tests only (skip slow/integration)
    python scripts/test.py test_card           # Run tests matching 'test_card'
    python scripts/test.py test_card.py        # Run specific test file
    python scripts/test.py tests/test_core/    # Run tests in directory
    python scripts/test.py -k "test_flush"     # Run tests matching pattern (pytest -k)
    python scripts/test.py --ts                # Run TypeScript type checking
    python scripts/test.py --all               # Run Python tests + TypeScript checks
    python scripts/test.py --list              # List available test files
    python scripts/test.py --status            # Check if containers are running

    # From Python/Claude:
    from scripts.test import run, ts, status, quick
    run()                    # Run all Python tests
    run("test_card")         # Run tests matching pattern
    quick()                  # Run fast tests only
    ts()                     # TypeScript type checking
    status()                 # Check container status

Common test patterns:
    run("test_card")              # Core card logic
    run("test_persistence")       # Database/storage tests
    run("test_prompt")            # Prompt system tests
    run("test_ai")                # AI player tests
    run("test_state_machine")     # Game state machine tests
    run("test_experiment")        # Experiment system tests
    run("llm/")                   # All LLM tests
"""

import subprocess
import sys
import os

# Slow test files to skip in quick mode (use --ignore for instant skip at collection)
SLOW_TESTS = [
    "tests/test_ai_memory.py",
    "tests/test_ai_resilience.py",
    "tests/test_chat_persistence.py",
    "tests/test_experiment_routes.py",
    "tests/test_experiment_variants.py",
    "tests/test_message_history_impact.py",
    "tests/test_personality_responses.py",
    "tests/test_reflection_system.py",
    "tests/test_tournament_flow.py",
    "tests/test_core/llm/test_assistant.py",
    "tests/test_core/llm/test_client.py",
]


def _run_cmd(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command and handle errors."""
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if capture and result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
    return result


def status() -> bool:
    """Check if Docker containers are running."""
    result = _run_cmd(["docker", "compose", "ps", "--format", "json"], capture=True)
    if result.returncode != 0:
        print("Error: Could not check container status")
        return False

    # Check for running backend
    if "backend" not in result.stdout or '"running"' not in result.stdout.lower():
        print("Warning: Backend container may not be running")
        print("Run: docker compose up -d")
        return False

    print("✓ Containers are running")
    return True


def run(pattern: str = "", verbose: bool = False, quick_mode: bool = False) -> int:
    """
    Run Python tests in Docker.

    Args:
        pattern: Test file/pattern to match (e.g., "test_card", "tests/test_core/")
        verbose: Enable verbose output (-v flag)
        quick_mode: Skip slow/integration tests

    Returns:
        Exit code (0 = success)
    """
    # Coverage flags only for full suite runs (no pattern, not quick mode)
    cov_flags = [
        "--cov=poker", "--cov=flask_app", "--cov=core",
        "--cov-report=term-missing:skip-covered",
        "--cov-fail-under=40",
    ]

    cmd = ["docker", "compose", "exec", "backend", "python", "-m", "pytest", "tests/"]

    if pattern:
        if pattern.endswith(".py"):
            # Specific file
            if not pattern.startswith("tests/"):
                pattern = f"tests/{pattern}"
            cmd = ["docker", "compose", "exec", "backend", "python", "-m", "pytest", pattern]
        elif "/" in pattern:
            # Directory path
            cmd = ["docker", "compose", "exec", "backend", "python", "-m", "pytest", pattern]
        else:
            # Pattern match: use pytest -k
            cmd += ["-k", pattern]
    else:
        # Full suite: add coverage
        cmd += cov_flags

    if verbose:
        cmd.append("-v")

    if quick_mode:
        # Use --ignore to skip slow test files entirely (faster than -k)
        cmd = ["docker", "compose", "exec", "backend", "python", "-m", "pytest", "tests/"]
        for slow_file in SLOW_TESTS:
            cmd += ["--ignore", slow_file]
        if verbose:
            cmd.append("-v")

    result = _run_cmd(cmd)
    return result.returncode


def quick(verbose: bool = False) -> int:
    """Run fast tests only (skip slow integration tests)."""
    return run(quick_mode=True, verbose=verbose)


def ts() -> int:
    """Run TypeScript type checking."""
    print("Running TypeScript type check...")
    cmd = ["docker", "compose", "exec", "frontend", "npx", "tsc", "--noEmit"]
    result = _run_cmd(cmd)
    if result.returncode == 0:
        print("✓ TypeScript: No type errors")
    return result.returncode


def all_tests(verbose: bool = False) -> int:
    """Run all tests (Python + TypeScript)."""
    print("=" * 60)
    print("Running Python tests...")
    print("=" * 60)
    py_result = run(verbose=verbose)

    print("\n" + "=" * 60)
    print("Running TypeScript checks...")
    print("=" * 60)
    ts_result = ts()

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Python tests: {'✓ PASSED' if py_result == 0 else '✗ FAILED'}")
    print(f"  TypeScript:   {'✓ PASSED' if ts_result == 0 else '✗ FAILED'}")
    print("=" * 60)

    return max(py_result, ts_result)


def list_tests() -> list[str]:
    """List available test files."""
    result = _run_cmd(
        ["docker", "compose", "exec", "backend", "find", "tests", "-name", "test*.py", "-type", "f"],
        capture=True
    )
    if result.returncode != 0:
        print("Error listing tests")
        return []

    files = sorted(result.stdout.strip().split("\n"))
    print(f"Available test files ({len(files)}):")
    for f in files:
        print(f"  {f}")
    return files


# CLI interface
if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        sys.exit(run())

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--status" in args:
        sys.exit(0 if status() else 1)

    if "--list" in args:
        list_tests()
        sys.exit(0)

    if "--ts" in args:
        sys.exit(ts())

    if "--all" in args:
        verbose = "-v" in args or "--verbose" in args
        sys.exit(all_tests(verbose=verbose))

    if "--quick" in args:
        verbose = "-v" in args or "--verbose" in args
        sys.exit(quick(verbose=verbose))

    # Check for -k pattern
    if "-k" in args:
        idx = args.index("-k")
        if idx + 1 < len(args):
            pattern = args[idx + 1]
            verbose = "-v" in args or "--verbose" in args
            cmd = ["docker", "compose", "exec", "backend", "python", "-m", "pytest",
                   "tests/", "-k", pattern]
            if verbose:
                cmd.append("-v")
            result = _run_cmd(cmd)
            sys.exit(result.returncode)

    # Treat first non-flag arg as pattern
    verbose = "-v" in args or "--verbose" in args
    pattern = next((a for a in args if not a.startswith("-")), "")
    sys.exit(run(pattern, verbose=verbose))
