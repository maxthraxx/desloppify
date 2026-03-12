"""Review guidance hooks for C/C++."""

from __future__ import annotations

import re

HOLISTIC_REVIEW_DIMENSIONS: list[str] = [
    "cross_module_architecture",
    "abstraction_fitness",
    "api_surface_coherence",
    "design_coherence",
]

REVIEW_GUIDANCE = {
    "patterns": [
        "Keep header and source responsibilities aligned.",
        "Watch for namespace drift and accidental cross-module leakage.",
        "Prefer explicit boundaries between platform, runtime, and library code.",
    ],
    "naming": "Use consistent type, namespace, and file naming conventions.",
}

MIGRATION_PATTERN_PAIRS: list[tuple[str, object, object]] = []
MIGRATION_MIXED_EXTENSIONS: set[str] = set()
LOW_VALUE_PATTERN = re.compile(
    r"^\s*(?:#pragma\s+once|#include\s+<[^>]+>\s*$)",
    re.MULTILINE,
)


def module_patterns(content: str) -> list[str]:
    """Extract module-like review anchors from C/C++ content."""
    del content
    return []


def api_surface(file_contents: dict[str, str]) -> dict:
    """Build a minimal API surface summary."""
    del file_contents
    return {}
