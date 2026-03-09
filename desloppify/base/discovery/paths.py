"""Public path and snippet helpers used by command/runtime code."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from desloppify.base import text_utils as _text_utils
from desloppify.base.runtime_state import current_runtime_context


def _default_project_root() -> Path:
    """Resolve default project root from current environment and CWD."""
    return Path(os.environ.get("DESLOPPIFY_ROOT", Path.cwd())).resolve()


def get_project_root() -> Path:
    """Return the active project root, checking RuntimeContext first."""
    override = current_runtime_context().project_root
    if override is not None:
        return Path(override).resolve()
    return _default_project_root()


def get_default_path() -> Path:
    """Return default scan path."""
    return get_project_root() / "src"


def get_src_path() -> Path:
    """Return the configured source root directory."""
    return get_project_root() / os.environ.get("DESLOPPIFY_SRC", "src")


class _PathProxy(os.PathLike[str]):
    """Backwards-compatible dynamic path reference."""

    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    def _path(self) -> Path:
        return self._resolver()

    def __fspath__(self) -> str:
        return str(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __repr__(self) -> str:
        return repr(self._path())

    def __truediv__(self, other: str | os.PathLike[str]) -> Path:
        return self._path() / other

    def __rtruediv__(self, other: str | os.PathLike[str]) -> Path:
        return Path(other) / self._path()

    def __eq__(self, other: object) -> bool:
        return self._path() == other

    def __hash__(self) -> int:
        return hash(self._path())

    def __getattr__(self, name: str) -> object:
        return getattr(self._path(), name)


# Deprecated compatibility exports: prefer get_project_root/get_default_path/get_src_path.
PROJECT_ROOT = _PathProxy(get_project_root)
DEFAULT_PATH = _PathProxy(get_default_path)
SRC_PATH = _PathProxy(get_src_path)


def read_code_snippet(
    filepath: str,
    line: int,
    context: int = 1,
    *,
    project_root: Path | str | None = None,
) -> str | None:
    """Read a snippet around a 1-based line number."""
    return _text_utils.read_code_snippet(
        filepath,
        line,
        context,
        project_root=(
            Path(project_root).resolve()
            if project_root is not None
            else get_project_root()
        ),
    )


def get_area(filepath: str, *, min_depth: int = 2) -> str:
    """Derive an area name from a file path (generic: first 2 components)."""
    text = (filepath or "").strip()
    if not text:
        return "(unknown)"
    parts = Path(text).parts
    if not parts:
        return "(unknown)"
    return "/".join(parts[:2]) if len(parts) >= min_depth else parts[0]


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_PATH",
    "SRC_PATH",
    "get_area",
    "get_project_root",
    "get_default_path",
    "get_src_path",
    "read_code_snippet",
]
