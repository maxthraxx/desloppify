"""C/C++ detector phase runners."""

from __future__ import annotations

from pathlib import Path

from desloppify.engine.detectors.base import ComplexitySignal
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.state_io import Issue

CXX_COMPLEXITY_SIGNALS = [
    ComplexitySignal("includes", r"(?m)^\s*#include\s+", weight=1, threshold=20),
]


def phase_structural(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Placeholder structural phase for the full-plugin scaffold."""
    del path, lang
    return [], {}
