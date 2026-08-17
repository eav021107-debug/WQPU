from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .constitution import CONSTITUTION
from .core import Controller, VPSControlError

_TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".md", ".mjs", ".php", ".prisma",
    ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx",
    ".vue", ".xml", ".yaml", ".yml",
}
_IMPORTANT_NAMES = {
    "dockerfile", "makefile", "procfile", "readme", "license", "gemfile", "rakefile",
    "package.json", "pyproject.toml", "cargo.toml", "go.mod", "go.sum", "requirements.txt",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", ".env.example",
}
_IGNORED_DIRS = {
    ".git", ".idea", ".next", ".nuxt", ".pytest_cache", ".ruff_cache", ".tox", ".venv",
    ".vscode", "__pycache__", "build", "coverage", "dist", "node_modules", "target", "vendor",
}
_IGNORED_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock"}
_STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "will", "have", "need", "make",
    "чтобы", "который", "которая", "которые", "это", "как", "для", "надо", "нужно", "сделать",
    "хочу", "замени", "заменить", "добавь", "добавить", "проект", "код", "работает", "работать",
}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[А-Яа-яЁё][А-Яа-яЁё0-9_]{2,}")


@dataclass
class FileRecord:
    path: str
    size: int
    mtime_ns: int
    digest: str
    content: str
    tokens: set[str]


class ProjectWorkspace:
    """Incremental in-memory view of the project used to build task-specific context bundles."""

    def __init__(
        self,
        controller: Controller,
        *,
        max_file_bytes: int = 256 * 1024,
        max_index_bytes: int = 32 * 1024 * 1024,
        max_files: int = 5000,
    ):
        self.controller = controller
        self.root = controller.settings.root
        self.max_file_bytes = max_file_bytes
        self.max_index_bytes = max_index_bytes
        self.max_files = max_files
        self._records: dict[str, FileRecord] = {}
        self._last_refresh = 0.0
        self._generation = 0

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOP_WORDS}

    @staticmethod
    def _is_text_candidate(path: Path) -> bool:
        name = path.name.lower()
        if name in _IGNORED_FILES:
            return False
        if name in _IMPORTANT_NAMES:
            return True
        return path.suffix.lower() in _TEXT_EXTENSIONS

    def _iter_candidates(self) -> Iterable[Path]:
        count = 0
        for current, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS and not d.startswith(".cache"))
            base = Path(current)
            for name in sorted(files):
                if count >= self.max_files:
                    return
                path = base / name
                if not self._is_text_candidate(path):
                    continue
                count += 1
                yield path

    def refresh(self) -> dict[str, Any]:
        seen: set[str] = set()
        total_bytes = 0
        changed = 0
        skipped = 0

        for path in self._iter_candidates():
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = self.controller.relative(path)
            seen.add(rel)
            if stat.st_size > self.max_file_bytes or total_bytes + stat.st_size > self.max_index_bytes:
                skipped += 1
                continue
            total_bytes += stat.st_size
            old = self._records.get(rel)
            if old and old.size == stat.st_size and old.mtime_ns == stat.st_mtime_ns:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            digest = hashlib.blake2b(content.encode("utf-8", errors="replace"), digest_size=8).hexdigest()
            self._records[rel] = FileRecord(
                path=rel,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                digest=digest,
                content=content,
                tokens=self._tokens(f"{rel}\n{content}"),
            )
            changed += 1

        removed = [path for path in self._records if path not in seen]
        for path in removed:
            self._records.pop(path, None)

        if changed or removed or not self._last_refresh:
            self._generation += 1
        self._last_refresh = time.time()
        return {
            "generation": self._generation,
            "indexed_files": len(self._records),
            "indexed_bytes": sum(record.size for record in self._records.values()),
            "changed": changed,
            "removed": len(removed),
            "skipped": skipped,
        }

    def invalidate(self, path: str | None = None) -> None:
        if path:
            try:
                rel = self.controller.relative(self.controller.resolve(path))
            except VPSControlError:
                rel = path
            self._records.pop(rel, None)
        self._last_refresh = 0.0

    def project_map(self, limit: int = 350) -> str:
        self.refresh()
        paths = sorted(self._records)
        shown = paths[: max(1, min(limit, 1000))]
        suffix = "" if len(paths) <= len(shown) else f"\n... +{len(paths) - len(shown)} more indexed files"
        return "\n".join(shown) + suffix

    def _score(self, record: FileRecord, task_tokens: set[str]) -> float:
        path_lower = record.path.lower()
        score = 0.0
        for token in task_tokens:
            if token in path_lower:
                score += 12.0
            if token in record.tokens:
                score += 3.0
        name = Path(record.path).name.lower()
        if name in _IMPORTANT_NAMES:
            score += 2.0
        if any(part in {"src", "app", "server", "client", "core", "api"} for part in Path(record.path).parts):
            score += 0.5
        return score

    def context_for_task(
        self,
        task: str,
        *,
        max_files: int = 18,
        max_chars: int = 110_000,
    ) -> dict[str, Any]:
        if not task.strip():
            raise VPSControlError("Task cannot be empty")
        stats = self.refresh()
        task_tokens = self._tokens(task)
        ranked = sorted(
            self._records.values(),
            key=lambda record: (self._score(record, task_tokens), -len(Path(record.path).parts), -record.size),
            reverse=True,
        )

        selected: list[FileRecord] = []
        selected_paths: set[str] = set()
        used = 0
        for record in ranked:
            score = self._score(record, task_tokens)
            important = Path(record.path).name.lower() in _IMPORTANT_NAMES
            if score <= 0 and not important and selected:
                continue
            block_size = len(record.content) + len(record.path) + 64
            if used + block_size > max_chars:
                continue
            selected.append(record)
            selected_paths.add(record.path)
            used += block_size
            if len(selected) >= max(1, min(max_files, 40)):
                break

        if not selected:
            for record in sorted(self._records.values(), key=lambda item: (len(Path(item.path).parts), item.path))[:8]:
                if used + len(record.content) > max_chars:
                    continue
                selected.append(record)
                selected_paths.add(record.path)
                used += len(record.content)

        files = [
            {
                "path": record.path,
                "digest": record.digest,
                "lines": record.content.count("\n") + (1 if record.content else 0),
                "content": record.content,
            }
            for record in selected
        ]
        git = self.controller.git_status(".")
        return {
            "constitution": CONSTITUTION,
            "task": task,
            "index": stats,
            "git_status": git,
            "project_map": self.project_map(),
            "selected_files": files,
            "selected_paths": sorted(selected_paths),
            "instruction": (
                "Treat this as the initial working set, not as proof that unrelated files are irrelevant. "
                "Plan from this context, use targeted fallback reads only when a concrete missing dependency is discovered, "
                "then modify, verify, search for leftovers, and review the final diff under the constitution."
            ),
        }
