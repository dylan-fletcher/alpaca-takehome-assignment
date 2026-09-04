"""House style checks that SQLFluff has no rule for.

Currently one: a select list is a single contiguous block. SQLFluff will
collapse two blank lines into one but has no config to forbid them outright,
so we walk its parse tree ourselves rather than leaving the rule as prose in
CLAUDE.md where nothing executes it.

Used by lint_sql.py; not meant to be run directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlfluff.core import FluffConfig, Linter

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"L:{self.line:>4} | {self.code} | {self.message}"


def _blank_lines_in_select(tree, path: Path) -> list[Finding]:
    """Flag blank lines between select targets.

    A blank line is two newline segments with nothing but whitespace between
    them. Comment banners (`---------- prices`) are the intended way to group
    a long select list; blank lines push it off a screen for no gain.
    """
    findings: list[Finding] = []
    for clause in tree.recursive_crawl("select_clause"):
        seen_newline = False
        for seg in clause.raw_segments:
            if seg.is_type("newline"):
                if seen_newline:
                    line = seg.pos_marker.source_position()[0]
                    findings.append(
                        Finding(
                            path,
                            line,
                            "SQ01",
                            "Blank line in a select list. Keep the targets "
                            "contiguous and group them with comments.",
                        )
                    )
                    seen_newline = False  # one finding per blank run
                else:
                    seen_newline = True
            elif not seg.is_type("whitespace"):
                seen_newline = False
    return findings


CHECKS = (_blank_lines_in_select,)


def check(path: Path) -> list[Finding]:
    """Parse `path` with the project's SQLFluff config and run every check."""
    # Anchored to ROOT, not to `path`: from_path() walks up from whatever it is
    # given, so a file outside the repo would otherwise find no .sqlfluff at
    # all and fail with "No dialect was specified".
    config = FluffConfig.from_path(str(ROOT))
    parsed = Linter(config=config).parse_string(path.read_text(), fname=str(path))
    if parsed.tree is None:
        return []  # unparseable: sqlfluff lint reports that far better than we can

    findings: list[Finding] = []
    for run in CHECKS:
        findings.extend(run(parsed.tree, path))
    return sorted(findings, key=lambda f: f.line)
