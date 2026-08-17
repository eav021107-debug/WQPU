from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .constitution import CONSTITUTION
from .core import Controller, Settings, VPSControlError
from .workspace import ProjectWorkspace

settings = Settings.from_env()
controller = Controller(settings)
workspace = ProjectWorkspace(controller)

mcp = MCPServer(
    "VPS Control",
    instructions=(
        "You are attached to a living software project, not a generic remote terminal. "
        "For every coding task, call open_project(task) first: it returns the current project map, Git state, "
        "the constitution, and the code most relevant to the user's idea in one bundle. "
        "Do not make the user explain implementation details they did not choose. Translate product intent into a clean implementation. "
        "Use targeted read/search only when the initial bundle reveals a concrete missing dependency. "
        "After any change, run relevant verification and review the final diff. "
        "The following constitution is mandatory and outranks convenience:\n\n" + CONSTITUTION
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


@mcp.resource("project://constitution")
def project_constitution() -> str:
    """The mandatory engineering constitution for this workspace."""
    return CONSTITUTION


@mcp.resource("project://map")
def project_map() -> str:
    """A live map of indexed project files."""
    return workspace.project_map()


@mcp.tool(title="Open living project", annotations=READ_ONLY)
def open_project(task: str, max_files: int = 18, max_chars: int = 110000) -> dict:
    """MANDATORY first step for coding tasks. Give the user's idea verbatim. Returns constitution + Git state + project map + task-relevant code in one bundle, so you normally do not need to request files one by one."""
    return _call(workspace.context_for_task, task, max_files=max_files, max_chars=max_chars)


@mcp.tool(title="Refresh project index", annotations=READ_ONLY)
def refresh_project() -> dict:
    """Refresh the living project index after external changes on the VPS."""
    return _call(workspace.refresh)


@mcp.tool(title="VPS status", annotations=READ_ONLY)
def status() -> dict:
    """Inspect the VPS and workspace health."""
    result = _call(controller.server_status)
    if isinstance(result, dict) and result.get("ok") is not False:
        result["project_index"] = _call(workspace.refresh)
    return result


@mcp.tool(title="List directory fallback", annotations=READ_ONLY)
def list_dir(path: str = ".", limit: int = 200) -> dict:
    """Fallback inspection tool. Prefer open_project(task) first."""
    return _call(controller.list_dir, path, limit)


@mcp.tool(title="Read file fallback", annotations=READ_ONLY)
def read_file(path: str, start_line: int = 1, end_line: int = 400) -> dict:
    """Fallback targeted read when open_project exposed a concrete missing dependency."""
    return _call(controller.read_file, path, start_line, end_line)


@mcp.tool(title="Search code fallback", annotations=READ_ONLY)
def search_text(query: str, path: str = ".", limit: int = 100) -> dict:
    """Fallback targeted search after the living project bundle has been inspected."""
    return _call(controller.search_text, query, path, limit)


@mcp.tool(title="Write project file", annotations=WRITE)
def write_file(path: str, content: str, create_parents: bool = True) -> dict:
    """Create or fully replace a text file inside the project. The living index is invalidated automatically."""
    result = _call(controller.write_file, path, content, create_parents)
    if isinstance(result, dict) and result.get("ok") is not False:
        workspace.invalidate(path)
    return result


@mcp.tool(title="Apply coherent code patch", annotations=WRITE)
def apply_patch(patch: str, cwd: str = ".") -> dict:
    """Apply a git-compatible unified diff for coherent multi-file edits. Re-indexes the project automatically."""
    result = _call(controller.apply_patch, patch, cwd)
    if isinstance(result, dict) and result.get("ok") is not False:
        workspace.invalidate()
    return result


@mcp.tool(title="Git status", annotations=READ_ONLY)
def git_status(cwd: str = ".") -> dict:
    """Inspect current branch and changed files."""
    return _call(controller.git_status, cwd)


@mcp.tool(title="Git diff", annotations=READ_ONLY)
def git_diff(cwd: str = ".", staged: bool = False) -> dict:
    """Review exact project changes. Always use before declaring a coding task complete."""
    return _call(controller.git_diff, cwd, staged)


@mcp.tool(title="Run project command", annotations=SHELL)
def run_command(command: str, cwd: str = ".", timeout: int | None = None) -> dict:
    """Run tests/build/lint/git/project commands as the VPS Control OS user. Shell commands may change project files, so the living index is invalidated automatically."""
    result = _call(controller.run_command, command, cwd, timeout)
    workspace.invalidate()
    return result


def main() -> None:
    host = os.getenv("VPS_CONTROL_HOST", "127.0.0.1")
    port = int(os.getenv("VPS_CONTROL_PORT", "8765"))
    workspace.refresh()
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
