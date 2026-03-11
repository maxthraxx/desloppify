"""Shared degraded-plan warning helpers for resolve flows."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from desloppify.base.output.terminal import colorize

@dataclass(frozen=True)
class DegradedPlanWarning:
    """Structured degraded-mode warning payload for resolve flows."""

    error_kind: str | None
    message: str
    behavior: str


@dataclass
class DegradedPlanWarningState:
    """Mutable per-call-chain dedupe state for degraded resolve warnings."""

    warned: bool = False


def warn_plan_load_degraded_once(
    *,
    error_kind: str | None,
    behavior: str,
    warning_state: DegradedPlanWarningState | None = None,
) -> DegradedPlanWarning | None:
    """Print one consistent warning when resolve behavior degrades.

    Returns a structured warning payload on first emission, else ``None``.
    Dedupe is scoped to the provided ``warning_state``; unrelated resolve
    attempts should pass separate state objects and will warn independently.
    """
    if warning_state is not None:
        if warning_state.warned:
            return None
        warning_state.warned = True

    detail = f" ({error_kind})" if error_kind else ""
    message = (
        "Warning: resolve is running in degraded mode because the living "
        f"plan could not be loaded{detail}."
    )
    warning = DegradedPlanWarning(
        error_kind=error_kind,
        message=message,
        behavior=behavior,
    )
    print(
        colorize(f"  {warning.message}", "yellow"),
        file=sys.stderr,
    )
    print(
        colorize(f"  {warning.behavior}", "dim"),
        file=sys.stderr,
    )
    return warning


def _reset_degraded_plan_warning_for_tests() -> None:
    """Backward-compatible no-op kept for existing tests."""
    return None


__all__ = [
    "DegradedPlanWarning",
    "DegradedPlanWarningState",
    "warn_plan_load_degraded_once",
]
