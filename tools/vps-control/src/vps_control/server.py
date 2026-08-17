from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .core import Controller, Settings, VPSControlError

settings = Settings.from_env()
controller = Controller(settings)

mcp = MCPServer(
    "VPS Control",
    instructions=(
        "Operate only inside the configured workspace unless the user explicitly asks for a shell command. "
        "Inspect before editing. After edits, run relevant tests or checks. Do not invent command results. "
        "Use read/list/search tools for inspection, write/apply_patch for changes, and run_command for terminal work."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
SHELL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except VPSControlError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(title="VPS status", annotations=READ_ONLY)
def status() -> dict:
    """Use this to inspect the VPS user, hostname, workspace, uptime, disk and memory."""
    return _call(controller.server_status)


@mcp.tool(title="List directory", annotations=READ_ONLY)
def list_dir(path: str = ".", limit: int = 200) -> dict:
    """Use this to inspect files and directories inside the configured VPS workspace."""
    return _call(controller.list_dir, path, limit)


@mcp.tool(title="Read file", annotations=READ_ONLY)
def read_file(path: str, start_line: int = 1, end_line: int = 400) -> dict:
    """Use this to read a text file from the VPS workspace. Prefer targeted line ranges for large files."""
    return _call(controller.read_file, path, start_line, end_line)


@mcp.tool(title="Search text", annotations=READ_ONLY)
def search_text(query: str, path: str = ".", limit: int = 100) -> dict:
    """Use this to find text across files in the VPS workspace before deciding what to edit."""
    return _call(controller.search_text, query, path, limit)


@mcp.tool(title="Write file", annotations=WRITE)
def write_file(path: str, content: str, create_parents: bool = True) -> dict:
    """Use this to create or fully replace a text file inside the VPS workspace."""
    return _call(controller.write_file, path, content, create_parents)


@mcp.tool(title="Apply patch", annotations=WRITE)
def apply_patch(patch: str, cwd: str = ".") -> dict:
    """Use this for precise multi-file code edits. The patch must be a git-compatible unified diff."""
    return _call(controller.apply_patch, patch, cwd)


@mcp.tool(title="Git status", annotations=READ_ONLY)
def git_status(cwd: str = ".") -> dict:
    """Use this to inspect the current branch and changed files in a git repository."""
    return _call(controller.git_status, cwd)


@mcp.tool(title="Git diff", annotations=READ_ONLY)
def git_diff(cwd: str = ".", staged: bool = False) -> dict:
    """Use this to review unstaged or staged git changes before and after edits."""
    return _call(controller.git_diff, cwd, staged)


@mcp.tool(title="Run terminal command", annotations=SHELL)
def run_command(command: str, cwd: str = ".", timeout: int | None = None) -> dict:
    """Use this when terminal execution is needed: install dependencies, run tests, git, Docker or project commands. Runs as the OS user hosting VPS Control, never as ChatGPT itself."""
    return _call(controller.run_command, command, cwd, timeout)


def main() -> None:
    host = os.getenv("VPS_CONTROL_HOST", "127.0.0.1")
    port = int(os.getenv("VPS_CONTROL_PORT", "8765"))
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
