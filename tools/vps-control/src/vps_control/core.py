from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_OUTPUT = 64 * 1024
MAX_FILE_READ = 512 * 1024


class VPSControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    root: Path
    command_timeout: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("VPS_CONTROL_ROOT", "/srv/vps-control-workspace")).expanduser().resolve()
        timeout = int(os.getenv("VPS_CONTROL_COMMAND_TIMEOUT", "120"))
        return cls(root=root, command_timeout=max(1, min(timeout, 900)))


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw: str = ".") -> Path:
        raw = raw or "."
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.settings.root)
        except ValueError as exc:
            raise VPSControlError(f"Path is outside workspace: {raw}") from exc
        return resolved

    def relative(self, path: Path) -> str:
        return "." if path == self.settings.root else str(path.relative_to(self.settings.root))

    def list_dir(self, path: str = ".", limit: int = 200) -> dict[str, Any]:
        p = self.resolve(path)
        if not p.exists():
            raise VPSControlError(f"Path does not exist: {path}")
        if not p.is_dir():
            raise VPSControlError(f"Not a directory: {path}")
        rows = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[: max(1, min(limit, 1000))]:
            try:
                stat = child.stat()
                size = stat.st_size
            except OSError:
                size = None
            rows.append({
                "name": child.name,
                "path": self.relative(child),
                "type": "dir" if child.is_dir() else "file" if child.is_file() else "other",
                "size": size,
            })
        return {"path": self.relative(p), "entries": rows}

    def read_file(self, path: str, start_line: int = 1, end_line: int = 400) -> dict[str, Any]:
        p = self.resolve(path)
        if not p.is_file():
            raise VPSControlError(f"Not a file: {path}")
        if p.stat().st_size > MAX_FILE_READ:
            raise VPSControlError(f"File is too large to read directly ({p.stat().st_size} bytes)")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, start_line)
        end = max(start, min(end_line, start + 1999))
        selected = lines[start - 1 : end]
        return {
            "path": self.relative(p),
            "start_line": start,
            "end_line": start + len(selected) - 1 if selected else start - 1,
            "total_lines": len(lines),
            "content": "\n".join(selected),
        }

    def write_file(self, path: str, content: str, create_parents: bool = True) -> dict[str, Any]:
        p = self.resolve(path)
        if create_parents:
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.parent.exists():
            raise VPSControlError(f"Parent directory does not exist: {self.relative(p.parent)}")
        p.write_text(content, encoding="utf-8")
        return {"path": self.relative(p), "bytes": len(content.encode("utf-8"))}

    def search_text(self, query: str, path: str = ".", limit: int = 100) -> dict[str, Any]:
        base = self.resolve(path)
        if not query:
            raise VPSControlError("Query cannot be empty")
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches: list[dict[str, Any]] = []
        files = [base] if base.is_file() else base.rglob("*")
        for item in files:
            if len(matches) >= max(1, min(limit, 500)):
                break
            if not item.is_file():
                continue
            try:
                if item.stat().st_size > MAX_FILE_READ:
                    continue
                text = item.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    matches.append({"path": self.relative(item), "line": number, "text": line[:500]})
                    if len(matches) >= max(1, min(limit, 500)):
                        break
        return {"query": query, "matches": matches}

    def run_command(self, command: str, cwd: str = ".", timeout: int | None = None) -> dict[str, Any]:
        if not command.strip():
            raise VPSControlError("Command cannot be empty")
        workdir = self.resolve(cwd)
        if not workdir.is_dir():
            raise VPSControlError(f"Not a directory: {cwd}")
        actual_timeout = max(1, min(timeout or self.settings.command_timeout, 900))
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=workdir,
                text=True,
                capture_output=True,
                timeout=actual_timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
            err = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
            raise VPSControlError(f"Command timed out after {actual_timeout}s\n{out[-4000:]}\n{err[-4000:]}") from exc
        stdout = proc.stdout[-MAX_OUTPUT:]
        stderr = proc.stderr[-MAX_OUTPUT:]
        return {
            "command": command,
            "cwd": self.relative(workdir),
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT,
        }

    def apply_patch(self, patch: str, cwd: str = ".") -> dict[str, Any]:
        if not patch.strip():
            raise VPSControlError("Patch cannot be empty")
        workdir = self.resolve(cwd)
        if not workdir.is_dir():
            raise VPSControlError(f"Not a directory: {cwd}")
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            cwd=workdir,
            input=patch,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if check.returncode != 0:
            raise VPSControlError(f"Patch check failed: {check.stderr.strip()}")
        apply = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=workdir,
            input=patch,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if apply.returncode != 0:
            raise VPSControlError(f"Patch failed: {apply.stderr.strip()}")
        return {"cwd": self.relative(workdir), "applied": True}

    def git_status(self, cwd: str = ".") -> dict[str, Any]:
        return self.run_command("git status --short --branch", cwd=cwd, timeout=30)

    def git_diff(self, cwd: str = ".", staged: bool = False) -> dict[str, Any]:
        cmd = "git diff --cached" if staged else "git diff"
        return self.run_command(cmd, cwd=cwd, timeout=30)

    def server_status(self) -> dict[str, Any]:
        checks = {}
        for name, cmd in {
            "whoami": "whoami",
            "hostname": "hostname",
            "uptime": "uptime -p 2>/dev/null || uptime",
            "disk": "df -h . | tail -1",
            "memory": "free -h 2>/dev/null | sed -n '1,2p' || true",
        }.items():
            checks[name] = self.run_command(cmd, timeout=15)["stdout"].strip()
        return {"workspace": str(self.settings.root), **checks}
