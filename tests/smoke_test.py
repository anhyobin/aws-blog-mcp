"""Smoke test for an installed aws-blog-mcp (run as a script, not by pytest).

Starts the installed ``aws-blog-mcp`` console script over stdio, connects with both protocol
modes (2026 discovery and the legacy initialize handshake), and checks the server identity and
the exact tool set. No tool is called, so no request reaches aws.amazon.com.

CI, against the built wheel::

    uv build
    uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py

Exits 0 on success and 1 on failure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import sysconfig
from importlib.metadata import version
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

__test__ = False  # matches pytest's default ``*_test.py`` pattern; there is nothing to collect

DIST_NAME = "aws-blog-mcp"
EXPECTED_TOOLS = frozenset({"search_posts", "list_recent_posts", "list_categories", "get_post"})
MODES = ("auto", "legacy")
TIMEOUT_SECONDS = 60.0


def server_command() -> list[str]:
    """Return the argv of the installed server, preferring this environment's console script."""
    scripts = Path(sysconfig.get_path("scripts"))
    for name in ("aws-blog-mcp", "aws-blog-mcp.exe"):
        candidate = scripts / name
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("aws-blog-mcp")
    if found:
        return [found]
    return [sys.executable, "-m", "aws_blog_mcp"]


def check_version_flag(argv: list[str], expected: str) -> list[str]:
    """Run ``--version`` and compare it with the installed distribution's version."""
    proc = subprocess.run(  # noqa: S603 - argv is this environment's own console script
        [*argv, "--version"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    want = f"aws-blog-mcp {expected}"
    if proc.returncode != 0 or proc.stdout.strip() != want:
        return [
            f"--version: exit {proc.returncode}, stdout {proc.stdout.strip()!r} "
            f"(expected {want!r}), stderr {proc.stderr.strip()!r}"
        ]
    return []


async def check_stdio(argv: list[str], mode: str, expected_version: str) -> list[str]:
    """Connect over stdio and return a list of problems (empty when everything is fine)."""
    params = StdioServerParameters(command=argv[0], args=argv[1:])
    with anyio.fail_after(TIMEOUT_SECONDS):
        async with Client(params, mode=mode) as client:
            info = client.server_info
            instructions = client.instructions
            listing = await client.list_tools()
    problems: list[str] = []
    names = {tool.name for tool in listing.tools}
    if names != EXPECTED_TOOLS:
        missing, unexpected = sorted(EXPECTED_TOOLS - names), sorted(names - EXPECTED_TOOLS)
        problems.append(f"[{mode}] tools: missing {missing}, unexpected {unexpected}")
    if info is None or info.name != DIST_NAME or info.version != expected_version:
        problems.append(f"[{mode}] server info: {info!r}, expected {DIST_NAME} {expected_version}")
    if not instructions:
        problems.append(f"[{mode}] the server sent no instructions")
    for tool in listing.tools:
        hints = tool.annotations
        if hints is None or hints.read_only_hint is not True:
            problems.append(f"[{mode}] {tool.name} is not annotated read-only")
    return problems


async def run() -> int:
    """Run every check and report; return the process exit code."""
    argv = server_command()
    expected = version(DIST_NAME)
    print(f"Checking {DIST_NAME} {expected} via: {' '.join(argv)}")
    problems = check_version_flag(argv, expected)
    for mode in MODES:
        try:
            problems += await check_stdio(argv, mode, expected)
        except Exception as exc:  # report any startup/handshake failure and keep going
            problems.append(f"[{mode}] could not talk to the server: {type(exc).__name__}: {exc}")
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    print(f"OK: {', '.join(sorted(EXPECTED_TOOLS))} over stdio ({', '.join(MODES)})")
    return 0


def main() -> None:
    """Script entry point."""
    sys.exit(anyio.run(run))


if __name__ == "__main__":
    main()
