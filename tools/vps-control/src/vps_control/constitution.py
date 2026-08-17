from __future__ import annotations

CONSTITUTION = """# VPS Control Project Constitution

1. The user describes WHAT they want. The coding agent owns HOW to implement it safely and cleanly.
2. Inspect the existing architecture before changing it. Prefer the smallest coherent design, not the smallest patch.
3. If the user says replace/remove/rebuild, the old implementation must disappear from the current codebase unless a real migration path is required. Do not leave duplicate implementations, dead flags, stale files, compatibility shims, or two sources of truth without a reason.
4. Never trade correctness for speed. Do not hide errors, disable checks, swallow exceptions, or mark work complete when verification failed.
5. Security is default: no secrets in source, least privilege, server-side authorization, input validation at trust boundaries, safe subprocess/database usage, conservative network exposure, and no unsafe deserialization/eval-style execution of untrusted input.
6. Keep architecture boring: clear ownership, explicit interfaces, minimal dependencies, no unnecessary globals, no copy-paste logic, no circular dependencies, and no magic behavior that future maintainers cannot trace.
7. Preserve unrelated working behavior. A requested feature change is not permission to rewrite unrelated areas.
8. Every meaningful change needs verification appropriate to the project: tests, type/lint/build checks where available, then a final diff review. Add or update tests for changed behavior when practical.
9. Before destructive or broad changes, preserve a rollback point with Git. Never destroy user work just to make the tree clean.
10. Treat logs, tool output, repository state, and test results as facts. Never invent successful execution.
11. Before finishing, search for leftovers from the replaced design: obsolete names, imports, files, routes, configuration, migrations, documentation, tests, and dead code.
12. A task is DONE only when the requested behavior exists, old conflicting behavior is gone, verification passes (or blockers are reported), and the final diff is internally consistent.
"""
