"""Unified work-queue selection for next/show/plan views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from desloppify.engine._plan.subjective_policy import (
    SubjectiveVisibility,
    compute_subjective_visibility,
)
from desloppify.engine._work_queue.helpers import (
    ALL_STATUSES,
    ATTEST_EXAMPLE,
    build_create_plan_item,
    build_score_checkpoint_item,
    build_subjective_items,
    build_triage_stage_items,
    scope_matches,
)
from desloppify.engine._work_queue.ranking import (
    build_finding_items,
    enrich_with_impact,
    group_queue_items,
    item_explain,
    item_sort_key,
)
from desloppify.engine._work_queue.plan_order import (
    apply_plan_order as _apply_plan_order,
    collapse_clusters as _collapse_clusters,
    new_item_ids as _new_item_ids,
)
from desloppify.state import StateModel


@dataclass(frozen=True)
class QueueBuildOptions:
    """Configuration for queue construction."""

    count: int | None = 1
    scan_path: str | None = None
    scope: str | None = None
    status: str = "open"
    include_subjective: bool = True
    subjective_threshold: float = 100.0
    chronic: bool = False
    explain: bool = False
    plan: dict | None = None
    include_skipped: bool = False
    cluster: str | None = None
    collapse_clusters: bool = True
    policy: SubjectiveVisibility | None = None


class WorkQueueResult(TypedDict):
    """Typed shape of the dict returned by :func:`build_work_queue`."""

    items: list[dict]
    total: int
    grouped: dict[str, list[dict]]
    new_ids: set[str]


def build_work_queue(
    state: StateModel,
    *,
    options: QueueBuildOptions | None = None,
) -> WorkQueueResult:
    """Build ranked queue items + tier metadata."""
    resolved_options = options or QueueBuildOptions()

    status = resolved_options.status
    if status not in ALL_STATUSES:
        raise ValueError(f"Unsupported status filter: {status}")
    try:
        subjective_threshold_value = float(resolved_options.subjective_threshold)
    except (TypeError, ValueError):
        subjective_threshold_value = 100.0
    subjective_threshold_value = max(0.0, min(100.0, subjective_threshold_value))

    finding_items = build_finding_items(
        state,
        scan_path=resolved_options.scan_path,
        status_filter=status,
        scope=resolved_options.scope,
        chronic=resolved_options.chronic,
    )

    all_items = list(finding_items)

    # Resolve subjective visibility policy for gating
    policy = resolved_options.policy or compute_subjective_visibility(state)

    if (
        resolved_options.include_subjective
        and status in {"open", "all"}
        and not resolved_options.chronic
    ):
        subjective_items = build_subjective_items(
            state,
            state.get("findings", {}),
            threshold=subjective_threshold_value,
        )
        # When a plan explicitly includes a subjective item in queue_order,
        # surface it regardless of policy — the plan is authoritative.
        plan_queue_set: set[str] = (
            set(resolved_options.plan.get("queue_order", []))
            if resolved_options.plan
            else set()
        )
        for item in subjective_items:
            if not scope_matches(item, resolved_options.scope):
                continue
            # Non-initial subjective items only surface when objective
            # backlog is drained — unless the plan explicitly queued them
            # (e.g. stale dims injected after a completed cycle).
            item_id = item.get("id", "")
            if not policy.should_surface(item) and item_id not in plan_queue_set:
                continue
            all_items.append(item)

    # Inject triage stage items and workflow items when plan requires it
    if resolved_options.plan and status in {"open", "all"}:
        synth_items = build_triage_stage_items(resolved_options.plan, state)
        all_items.extend(synth_items)
        checkpoint_item = build_score_checkpoint_item(resolved_options.plan, state)
        if checkpoint_item is not None:
            all_items.append(checkpoint_item)
        plan_item = build_create_plan_item(resolved_options.plan)
        if plan_item is not None:
            all_items.append(plan_item)

    enrich_with_impact(all_items, state.get("dimension_scores", {}))

    # Impact floor: drop mechanical findings with negligible score impact
    MIN_STANDALONE_IMPACT = 0.05
    all_items = [
        item for item in all_items
        if item.get("kind") != "finding"
        or item.get("is_review")
        or item.get("is_subjective")
        or not item.get("estimated_impact")
        or item["estimated_impact"] >= MIN_STANDALONE_IMPACT
    ]

    all_items.sort(key=item_sort_key)

    # Apply living plan ordering if provided
    new_ids: set[str] = set()
    if resolved_options.plan:
        new_ids = _new_item_ids(state)
        all_items = _apply_plan_order(
            all_items,
            resolved_options.plan,
            include_skipped=resolved_options.include_skipped,
            cluster=resolved_options.cluster,
            new_ids=new_ids,
        )

    # Collapse auto-clusters into meta-items (unless drilling into a cluster)
    should_collapse = (
        resolved_options.collapse_clusters
        and resolved_options.plan
        and not resolved_options.cluster
        and not resolved_options.plan.get("active_cluster")
    )
    if should_collapse:
        all_items = _collapse_clusters(all_items, resolved_options.plan)

    total = len(all_items)
    if resolved_options.count is not None and resolved_options.count > 0:
        all_items = all_items[: resolved_options.count]

    if resolved_options.explain:
        for item in all_items:
            item["explain"] = item_explain(item)

    return {
        "items": all_items,
        "total": total,
        "grouped": group_queue_items(all_items, "item"),
        "new_ids": new_ids,
    }


__all__ = [
    "ATTEST_EXAMPLE",
    "QueueBuildOptions",
    "WorkQueueResult",
    "build_work_queue",
    "group_queue_items",
]
