"""Lint SQL files: SQLFluff, plus the house checks in sql_style.py.

    uv run -- python scripts/lint_sql.py models/staging/binance/foo.sql
    uv run -- python scripts/lint_sql.py            # every tracked .sql file
    uv run -- python scripts/lint_sql.py --hook     # read a hook payload
    uv run -- python scripts/lint_sql.py --staged   # lint what is staged

Exits 1 if anything is reported, so it works in CI.

Both hook modes exit 2 instead, the code that feeds stderr back to Claude as a
blocking error. `--hook` reads a PostToolUse payload on stdin and lints the file
just written; anything that is not a .sql file exits 0 silently. `--staged`
lints the .sql files staged for commit and is wired to `git commit` as a
PreToolUse hook. Both live in .claude/settings.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sql_style  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKIP = ("dbt_packages", "target", "logs", ".venv")


def discover() -> list[Path]:
    return sorted(
        p
        for p in ROOT.rglob("*.sql")
        if not any(part in SKIP for part in p.relative_to(ROOT).parts)
    )


def hook_path() -> Path | None:
    """The file a PostToolUse payload on stdin refers to, if it is SQL."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    raw = payload.get("tool_response", {}).get("filePath") or payload.get(
        "tool_input", {}
    ).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.suffix == ".sql" and path.is_file() else None


def staged_paths() -> list[Path]:
    """The .sql files staged for commit, as absolute paths."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "--", "*.sql"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [
        path
        for name in result.stdout.split("\n")
        if name.strip() and (path := ROOT / name.strip()).is_file()
    ]


def main(argv: list[str]) -> int:
    if argv[:1] == ["--staged"]:
        # Deletions and unstaged edits are deliberately out of scope: this gates
        # what is about to be committed, not the working tree.
        return 2 if run(staged_paths()) else 0

    if argv[:1] == ["--hook"]:
        path = hook_path()
        # Exit 0, not 2: a non-SQL edit is not a failure, it is none of our
        # business. Only a real finding should interrupt Claude.
        return 2 if path and run([path]) else 0

    paths = [Path(a).resolve() for a in argv] if argv else discover()
    return 1 if run([p for p in paths if p.suffix == ".sql" and p.is_file()]) else 0


def run(paths: list[Path]) -> bool:
    """Lint `paths`, printing findings to stderr. True if anything was found."""
    if not paths:
        return False

    # A subprocess rather than the Python API, so output is byte-for-byte what
    # `uv run -- sqlfluff lint` prints. `-m` rather than the `sqlfluff` script,
    # so this does not depend on the venv being on PATH -- it is not, under a
    # bare `python scripts/lint_sql.py`.
    sqlfluff = subprocess.run(
        [sys.executable, "-m", "sqlfluff", "lint", *[str(p) for p in paths]],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    failed = sqlfluff.returncode != 0
    if failed:
        print(sqlfluff.stdout.strip(), file=sys.stderr)

    for path in paths:
        findings = sql_style.check(path)
        if not findings:
            continue
        failed = True
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        print(f"\n== [{rel}] FAIL", file=sys.stderr)
        for finding in findings:
            print(finding, file=sys.stderr)

    return failed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
