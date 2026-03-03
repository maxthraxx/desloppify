"""Scorecard dimension ordering and language policy constants.

Re-exports from the canonical engine-layer module to avoid duplication.
"""

from __future__ import annotations

from desloppify.engine.planning.scorecard_policy import (  # noqa: F401
    _DEFAULT_ELEGANCE_COMPONENTS,
    _ELEGANCE_COMPONENTS_BY_LANG,
    _MECHANICAL_SCORECARD_DIMENSIONS,
    _SCORECARD_DIMENSIONS_BY_LANG,
    _SCORECARD_MAX_DIMENSIONS,
    _SUBJECTIVE_SCORECARD_ORDER_BY_LANG,
    _SUBJECTIVE_SCORECARD_ORDER_DEFAULT,
    _compose_scorecard_dimensions,
)

__all__ = [
    "_DEFAULT_ELEGANCE_COMPONENTS",
    "_ELEGANCE_COMPONENTS_BY_LANG",
    "_MECHANICAL_SCORECARD_DIMENSIONS",
    "_SCORECARD_DIMENSIONS_BY_LANG",
    "_SCORECARD_MAX_DIMENSIONS",
    "_SUBJECTIVE_SCORECARD_ORDER_BY_LANG",
    "_SUBJECTIVE_SCORECARD_ORDER_DEFAULT",
    "_compose_scorecard_dimensions",
]
