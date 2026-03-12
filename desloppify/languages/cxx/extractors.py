"""C/C++ file discovery and function extraction scaffolds."""

from __future__ import annotations

from pathlib import Path

from desloppify.base.discovery.source import SourceDiscoveryOptions, find_source_files
from desloppify.engine.detectors.base import FunctionInfo

CXX_FILE_EXCLUSIONS = [
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    ".git",
    "node_modules",
]


def find_cxx_files(path: Path | str) -> list[str]:
    """Find C/C++ source files under a path."""
    return find_source_files(
        path,
        [".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"],
        SourceDiscoveryOptions(exclusions=tuple(CXX_FILE_EXCLUSIONS)),
    )


def extract_all_cxx_functions(path_or_files: Path | list[str]) -> list[FunctionInfo]:
    """Placeholder extractor kept intentionally minimal for Task 1 scaffold."""
    del path_or_files
    return []
