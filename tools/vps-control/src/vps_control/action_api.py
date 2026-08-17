from __future__ import annotations

import os
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .constitution import CONSTITUTION
from .core import Controller, Settings, VPSControlError
from .workspace import ProjectWorkspace

settings = Settings.from_env()
controller = Controller(settings)
workspace = ProjectWorkspace(controller)


def _result(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        value = fn(*args, **kwargs)
        if isinstance(value, dict):
            return value
        return {"result": value}
    except VPSControlError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _authorized(request: Request) -> bool:
    expected = os.getenv("VPS_CONTROL_ACTION_TOKEN", "")
    if not expected:
        return False
    return request.headers.get("x-vps-control-key", "") == expected


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "VPS Control", "workspace": str(settings.root)})


async def openapi_schema(_: Request) -> JSONResponse:
    public_url = os.getenv("VPS_CONTROL_PUBLIC_URL", "https://CHANGE-ME.example.com").rstrip("/")
    schema = {
        "openapi": "3.1.0",
        "info": {
            "title": "VPS Control Living Project",
            "version": "0.2.0",
            "description": "A project-aware coding bridge. The project is indexed on the VPS; openProject returns the constitution, project map, Git state and task-relevant code in one bundle.",
        },
        "servers": [{"url": public_url}],
        "components": {
            "securitySchemes": {
                "VPSControlKey": {"type": "apiKey", "in": "header", "name": "X-VPS-Control-Key"}
            }
        },
        "security": [{"VPSControlKey": []}],
        "paths": {
            "/v1/project/open": {
                "post": {
                    "operationId": "openProject",
                    "summary": "Open the living project for a coding task",
                    "description": "ALWAYS call this first for a coding task. Send the user's idea verbatim. It returns the mandatory constitution, current Git state, a live project map, and the code most relevant to the task in one response. Do not ask the user to choose implementation details unless truly necessary.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["task"], "properties": {
                            "task": {"type": "string"},
                            "max_files": {"type": "integer", "minimum": 1, "maximum": 30, "default": 18},
                            "max_chars": {"type": "integer", "minimum": 10000, "maximum": 120000, "default": 90000}
                        }
                    }}}},
                    "responses": {"200": {"description": "Project context bundle"}},
                }
            },
            "/v1/project/write": {
                "post": {
                    "operationId": "writeProjectFile",
                    "summary": "Create or replace a project text file",
                    "description": "Use for a deliberate full-file replacement. The project index is invalidated automatically.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["path", "content"], "properties": {
                            "path": {"type": "string"}, "content": {"type": "string"}
                        }
                    }}}},
                    "responses": {"200": {"description": "Write result"}},
                }
            },
            "/v1/project/patch": {
                "post": {
                    "operationId": "applyProjectPatch",
                    "summary": "Apply a coherent multi-file patch",
                    "description": "Preferred edit method for precise code changes. Accepts a git-compatible unified diff and refreshes the living project index.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["patch"], "properties": {
                            "patch": {"type": "string"}, "cwd": {"type": "string", "default": "."}
                        }
                    }}}},
                    "responses": {"200": {"description": "Patch result"}},
                }
            },
            "/v1/project/run": {
                "post": {
                    "operationId": "runProjectCommand",
                    "summary": "Run tests, build, lint, Git or a project command",
                    "description": "Use for project verification and necessary development commands. Runs as the restricted VPS Control OS user, not root. The project index is invalidated afterward.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["command"], "properties": {
                            "command": {"type": "string"}, "cwd": {"type": "string", "default": "."},
                            "timeout": {"type": "integer", "minimum": 1, "maximum": 900}
                        }
                    }}}},
                    "responses": {"200": {"description": "Command result"}},
                }
            },
            "/v1/project/diff": {
                "get": {
                    "operationId": "getGitDiff",
                    "summary": "Review the current Git diff",
                    "description": "ALWAYS use before declaring a coding task done. Check that the requested behavior is implemented and obsolete code is gone.",
                    "parameters": [
                        {"name": "cwd", "in": "query", "schema": {"type": "string", "default": "."}},
                        {"name": "staged", "in": "query", "schema": {"type": "boolean", "default": False}}
                    ],
                    "responses": {"200": {"description": "Git diff"}},
                }
            },
            "/v1/project/search": {
                "get": {
                    "operationId": "searchProjectFallback",
                    "summary": "Targeted fallback code search",
                    "description": "Use only when openProject reveals a concrete missing dependency or obsolete implementation that must be located.",
                    "parameters": [
                        {"name": "query", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "path", "in": "query", "schema": {"type": "string", "default": "."}}
                    ],
                    "responses": {"200": {"description": "Search results"}},
                }
            },
        },
        "x-vps-control-constitution": CONSTITUTION,
    }
    return JSONResponse(schema)


async def open_project(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await request.json()
    return JSONResponse(_result(
        workspace.context_for_task,
        str(body.get("task", "")),
        max_files=int(body.get("max_files", 18)),
        max_chars=int(body.get("max_chars", 90000)),
    ))


async def write_project_file(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await request.json()
    result = _result(controller.write_file, str(body.get("path", "")), str(body.get("content", "")), True)
    if result.get("ok") is not False:
        workspace.invalidate(str(body.get("path", "")))
    return JSONResponse(result)


async def patch_project(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await request.json()
    result = _result(controller.apply_patch, str(body.get("patch", "")), str(body.get("cwd", ".")))
    if result.get("ok") is not False:
        workspace.invalidate()
    return JSONResponse(result)


async def run_project(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    body = await request.json()
    timeout = body.get("timeout")
    result = _result(
        controller.run_command,
        str(body.get("command", "")),
        str(body.get("cwd", ".")),
        int(timeout) if timeout is not None else None,
    )
    workspace.invalidate()
    return JSONResponse(result)


async def git_diff(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    staged = request.query_params.get("staged", "false").lower() in {"1", "true", "yes"}
    return JSONResponse(_result(controller.git_diff, request.query_params.get("cwd", "."), staged))


async def search_project(request: Request) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return JSONResponse(_result(
        controller.search_text,
        request.query_params.get("query", ""),
        request.query_params.get("path", "."),
        100,
    ))


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/openapi.json", openapi_schema, methods=["GET"]),
    Route("/v1/project/open", open_project, methods=["POST"]),
    Route("/v1/project/write", write_project_file, methods=["POST"]),
    Route("/v1/project/patch", patch_project, methods=["POST"]),
    Route("/v1/project/run", run_project, methods=["POST"]),
    Route("/v1/project/diff", git_diff, methods=["GET"]),
    Route("/v1/project/search", search_project, methods=["GET"]),
]

app = Starlette(routes=routes)


def main() -> None:
    if not os.getenv("VPS_CONTROL_ACTION_TOKEN"):
        raise RuntimeError("VPS_CONTROL_ACTION_TOKEN must be set")
    workspace.refresh()
    host = os.getenv("VPS_CONTROL_ACTION_HOST", "127.0.0.1")
    port = int(os.getenv("VPS_CONTROL_ACTION_PORT", "8766"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
