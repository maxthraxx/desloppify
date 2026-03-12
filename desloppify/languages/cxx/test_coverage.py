"""C/C++-specific test coverage heuristics and mappings."""

from __future__ import annotations

import re

ASSERT_PATTERNS = [re.compile(r"\bASSERT_[A-Z_]+\b"), re.compile(r"\bEXPECT_[A-Z_]+\b")]
MOCK_PATTERNS = [re.compile(r"\bMOCK_METHOD\b"), re.compile(r"\bFakeIt\b")]
SNAPSHOT_PATTERNS: list[re.Pattern[str]] = []
TEST_FUNCTION_RE = re.compile(r"\bTEST(?:_F|_P)?\s*\(")
BARREL_BASENAMES: set[str] = set()


def has_testable_logic(filepath: str, content: str) -> bool:
    """Return True when a file looks like it contains runtime logic."""
    del filepath
    return bool(re.search(r"\b(?:class|struct|enum|namespace)\b", content))


def resolve_import_spec(
    spec: str,
    test_path: str,
    production_files: set[str],
) -> str | None:
    """Best-effort include resolution placeholder."""
    del spec, test_path, production_files
    return None


def resolve_barrel_reexports(filepath: str, production_files: set[str]) -> set[str]:
    """C/C++ has no barrel-file re-export expansion."""
    del filepath, production_files
    return set()


def parse_test_import_specs(content: str) -> list[str]:
    """Return include-like specs from test content."""
    del content
    return []


def map_test_to_source(test_path: str, production_set: set[str]) -> str | None:
    """Placeholder convention mapper for Task 1 scaffold."""
    del test_path, production_set
    return None


def strip_test_markers(basename: str) -> str | None:
    """Placeholder basename mapper for Task 1 scaffold."""
    del basename
    return None


def strip_comments(content: str) -> str:
    """Return content unchanged until the coverage hooks are implemented."""
    return content
