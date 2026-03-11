"""Lifecycle visibility filtering for work-queue items.

The queue has one explicit cycle:
``scan -> review -> workflow -> triage -> execute -> scan``.

Two details matter:
- ``review`` can be entered either from a fresh scan with initial subjective
  assessment placeholders, or after execute drains and post-flight review work
  exists. The phase name is the same, but the visible items differ.
- deferred disposition is treated as a blocker inside the ``scan`` boundary.
  It must be cleared before the scan step is considered available.
"""

from __future__ import annotations

from desloppify.engine.plan_queue import (
    current_lifecycle_phase,
    LIFECYCLE_PHASE_EXECUTE,
    LIFECYCLE_PHASE_REVIEW,
    LIFECYCLE_PHASE_SCAN,
    LIFECYCLE_PHASE_TRIAGE,
    LIFECYCLE_PHASE_WORKFLOW,
    NON_OBJECTIVE_DETECTORS,
    WORKFLOW_DEFERRED_DISPOSITION_ID,
    WORKFLOW_RUN_SCAN_ID,
)
from desloppify.engine._work_queue.types import WorkQueueItem

# Non-objective detectors that belong to the post-flight phase once objective
# execution work is drained.
# Must be a subset of NON_OBJECTIVE_DETECTORS.
POSTFLIGHT_NON_OBJECTIVE_DETECTORS: frozenset[str] = NON_OBJECTIVE_DETECTORS


def _validate_postflight_non_objective_detectors() -> None:
    missing = POSTFLIGHT_NON_OBJECTIVE_DETECTORS - NON_OBJECTIVE_DETECTORS
    if missing:
        raise RuntimeError(
            "POSTFLIGHT_NON_OBJECTIVE_DETECTORS has items not in NON_OBJECTIVE_DETECTORS: "
            f"{missing}"
        )


_validate_postflight_non_objective_detectors()


def _has_objective_items(items: list[WorkQueueItem]) -> bool:
    """True if any objective mechanical work items remain in the queue.

    Checks both individual issues and collapsed clusters — clusters
    contain objective issues grouped by the queue builder.
    """
    return any(
        item.get("kind") in ("issue", "cluster")
        and item.get("detector", "") not in NON_OBJECTIVE_DETECTORS
        for item in items
    )


def _has_initial_reviews(items: list[WorkQueueItem]) -> bool:
    """True if any unassessed subjective dimensions need initial review."""
    return any(
        item.get("kind") == "subjective_dimension"
        and item.get("initial_review")
        for item in items
    )


def _is_postflight_non_objective_item(item: WorkQueueItem) -> bool:
    """True if this item belongs to the non-objective post-flight review phase."""
    if item.get("kind") == "subjective_dimension":
        return not item.get("initial_review")
    return item.get("detector", "") in POSTFLIGHT_NON_OBJECTIVE_DETECTORS


def _has_postflight_non_objective_items(items: list[WorkQueueItem]) -> bool:
    """True if any non-objective post-flight review items are pending."""
    return any(_is_postflight_non_objective_item(item) for item in items)


def _has_triage_stages(items: list[WorkQueueItem]) -> bool:
    """True if any pending triage stage items are in the queue."""
    return any(
        item.get("kind") == "workflow_stage"
        and str(item.get("id", "")).startswith("triage::")
        for item in items
    )


def _is_deferred_disposition(item: WorkQueueItem) -> bool:
    return item.get("id") == WORKFLOW_DEFERRED_DISPOSITION_ID


def _has_deferred_disposition(items: list[WorkQueueItem]) -> bool:
    return any(_is_deferred_disposition(item) for item in items)


def _is_postflight_scan(item: WorkQueueItem) -> bool:
    return item.get("id") == WORKFLOW_RUN_SCAN_ID


def _has_postflight_scan(items: list[WorkQueueItem]) -> bool:
    return any(_is_postflight_scan(item) for item in items)


def _is_triage_stage(item: WorkQueueItem) -> bool:
    """True when item is a triage workflow stage."""
    return (
        item.get("kind") == "workflow_stage"
        and str(item.get("id", "")).startswith("triage::")
    )


def _is_postflight_workflow(item: WorkQueueItem) -> bool:
    return (
        item.get("kind") == "workflow_action"
        and not _is_deferred_disposition(item)
        and not _is_postflight_scan(item)
    )


def _has_postflight_workflow(items: list[WorkQueueItem]) -> bool:
    return any(_is_postflight_workflow(item) for item in items)


def _is_force_visible(item: WorkQueueItem) -> bool:
    """True when the item is explicitly escalated past objective gating."""
    return bool(item.get("force_visible"))


def _is_postflight_phase_item(item: WorkQueueItem) -> bool:
    return (
        _is_postflight_non_objective_item(item)
        or _is_triage_stage(item)
        or _is_deferred_disposition(item)
        or _is_postflight_scan(item)
        or _is_postflight_workflow(item)
    )


def resolve_lifecycle_phase(
    items: list[WorkQueueItem],
    *,
    plan: dict | None = None,
) -> str:
    """Resolve the active queue lifecycle phase from current items + plan state."""
    if _has_initial_reviews(items):
        return LIFECYCLE_PHASE_REVIEW
    if _has_objective_items(items):
        return LIFECYCLE_PHASE_EXECUTE
    if _has_deferred_disposition(items) or _has_postflight_scan(items):
        return LIFECYCLE_PHASE_SCAN
    if _has_postflight_non_objective_items(items):
        return LIFECYCLE_PHASE_REVIEW
    if _has_postflight_workflow(items):
        return LIFECYCLE_PHASE_WORKFLOW
    if _has_triage_stages(items):
        return LIFECYCLE_PHASE_TRIAGE
    persisted = current_lifecycle_phase(plan) if isinstance(plan, dict) else None
    if persisted is not None:
        return persisted
    return LIFECYCLE_PHASE_SCAN


def apply_lifecycle_filter(
    items: list[WorkQueueItem],
    *,
    plan: dict | None = None,
) -> list[WorkQueueItem]:
    """Enforce lifecycle visibility rules from the resolved explicit phase."""
    phase = resolve_lifecycle_phase(items, plan=plan)

    if phase == LIFECYCLE_PHASE_REVIEW:
        if _has_initial_reviews(items):
            return [
                item for item in items
                if item.get("kind") == "subjective_dimension" and item.get("initial_review")
            ]
        return [
            item for item in items
            if _is_postflight_non_objective_item(item) or _is_force_visible(item)
        ]

    if phase == LIFECYCLE_PHASE_EXECUTE:
        return [
            item for item in items
            if not _is_postflight_phase_item(item) or _is_force_visible(item)
        ]

    if phase == LIFECYCLE_PHASE_SCAN:
        if _has_deferred_disposition(items):
            return [
                item for item in items
                if _is_deferred_disposition(item) or _is_force_visible(item)
            ]
        return [
            item for item in items
            if _is_postflight_scan(item) or _is_force_visible(item)
        ]

    if phase == LIFECYCLE_PHASE_WORKFLOW:
        return [
            item for item in items
            if _is_postflight_workflow(item) or _is_force_visible(item)
        ]

    if phase == LIFECYCLE_PHASE_TRIAGE:
        return [item for item in items if _is_triage_stage(item) or _is_force_visible(item)]

    # Explicit post-flight scan phase without an item should fall back to empty.
    if _has_postflight_scan(items):
        return [
            item for item in items
            if _is_postflight_scan(item) or _is_force_visible(item)
        ]
    return []


__all__ = ["apply_lifecycle_filter", "resolve_lifecycle_phase"]
