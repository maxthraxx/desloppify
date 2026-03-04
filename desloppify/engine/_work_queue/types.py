"""Typed contracts for unified work-queue items."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, TypedDict

QueueItemKind: TypeAlias = Literal[
    "issue",
    "cluster",
    "workflow_stage",
    "workflow_action",
    "subjective_dimension",
]


class PlanClusterRef(TypedDict, total=False):
    """Plan-cluster metadata stamped onto queue items."""

    name: str
    description: str | None
    total_items: int
    action_steps: list[str]


class WorkQueueItem(TypedDict, total=False):
    """Unified queue item shape used across ranking/ordering/rendering."""

    id: str
    detector: str
    file: str
    tier: int
    confidence: str
    summary: str
    detail: dict[str, Any]
    status: str
    note: str | None
    first_seen: str
    last_seen: str
    resolved_at: str | None
    reopen_count: int
    suppressed: bool
    lang: str
    kind: QueueItemKind

    # Ranking + policy metadata
    is_review: bool
    is_subjective: bool
    review_weight: float | None
    subjective_score: float | None
    estimated_impact: float
    primary_command: str
    action_type: str
    explain: dict[str, Any]

    # Plan-order metadata
    _plan_position: int | None
    _is_new: bool
    queue_position: int
    plan_description: str
    plan_note: str
    plan_cluster: PlanClusterRef
    plan_skipped: bool
    plan_skip_kind: str
    plan_skip_reason: str

    # Cluster metadata
    members: list["WorkQueueItem"]
    member_count: int
    cluster_name: str
    cluster_auto: bool
    cluster_optional: bool

    # Workflow-stage metadata
    stage_name: str
    stage_index: int
    blocked_by: list[str]
    is_blocked: bool

    # Subjective-workflow metadata
    initial_review: bool
    cli_keys: list[str]
    dimension: str
    dimension_name: str
    strict: float
    score: float
    failing: int
    timestamp: str
    placeholder: bool
    stale: bool

    # Optional passthrough keys observed in queue item payloads
    action: str
    active_cluster: str | None
    auto: bool
    cluster: str
    clusters: dict[str, Any]
    count: int
    description: str
    dimension_scores: dict[str, Any]
    entries: list[Any]
    epic_triage_meta: dict[str, Any]
    fixers: list[str]
    issue_ids: list[str]
    issues: dict[str, Any]
    lang_capabilities: dict[str, Any]
    name: str
    optional: bool
    overall_per_point: float
    plan_start_scores: dict[str, Any]
    queue_order: list[str]
    reason: str
    scan_history: list[dict[str, Any]]
    scan_path: str | None
    skipped: dict[str, Any]
    triage_stages: dict[str, Any]


# Semantic aliases for readability at call sites.
IssueQueueItem: TypeAlias = WorkQueueItem
SubjectiveDimensionItem: TypeAlias = WorkQueueItem
WorkflowStageItem: TypeAlias = WorkQueueItem
WorkflowActionItem: TypeAlias = WorkQueueItem
ClusterQueueItem: TypeAlias = WorkQueueItem

WorkQueueGroups: TypeAlias = dict[str, list[WorkQueueItem]]


__all__ = [
    "ClusterQueueItem",
    "IssueQueueItem",
    "PlanClusterRef",
    "QueueItemKind",
    "SubjectiveDimensionItem",
    "WorkflowActionItem",
    "WorkflowStageItem",
    "WorkQueueGroups",
    "WorkQueueItem",
]
