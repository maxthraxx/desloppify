"""Focused public plan API for queue lifecycle and sync operations."""

from __future__ import annotations

from desloppify.engine._plan.auto_cluster import AUTO_PREFIX, auto_cluster_issues
from desloppify.engine._plan.constants import (
    SYNTHETIC_PREFIXES,
    WORKFLOW_COMMUNICATE_SCORE_ID,
    WORKFLOW_CREATE_PLAN_ID,
    WORKFLOW_DEFERRED_DISPOSITION_ID,
    WORKFLOW_IDS,
    WORKFLOW_IMPORT_SCORES_ID,
    WORKFLOW_PREFIX,
    WORKFLOW_RUN_SCAN_ID,
    WORKFLOW_SCORE_CHECKPOINT_ID,
    QueueSyncResult,
    confirmed_triage_stage_names,
    normalize_queue_workflow_and_triage_prefix,
    recorded_unconfirmed_triage_stage_names,
)
from desloppify.engine._plan.operations.lifecycle import purge_ids
from desloppify.engine._plan.operations.meta import append_log_entry
from desloppify.engine._plan.persistence import (
    has_living_plan,
    load_plan,
    plan_path_for_state,
    save_plan,
)
from desloppify.engine._plan.reconcile import ReconcileResult, reconcile_plan_after_scan
from desloppify.engine._plan.reconcile_review_import import (
    ReviewImportSyncResult,
    sync_plan_after_review_import,
)
from desloppify.engine._plan.refresh_lifecycle import (
    clear_postflight_scan_completion,
    current_lifecycle_phase,
    LIFECYCLE_PHASE_EXECUTE,
    LIFECYCLE_PHASE_REVIEW,
    LIFECYCLE_PHASE_SCAN,
    LIFECYCLE_PHASE_TRIAGE,
    LIFECYCLE_PHASE_WORKFLOW,
    mark_postflight_scan_completed,
    postflight_scan_pending,
    resolve_lifecycle_phase,
    set_lifecycle_phase,
    sync_lifecycle_phase,
)
from desloppify.engine._plan.policy.stale import open_review_ids, review_issue_snapshot_hash
from desloppify.engine._plan.policy.subjective import (
    NON_OBJECTIVE_DETECTORS,
    SubjectiveVisibility,
    compute_subjective_visibility,
)
from desloppify.engine._plan.sync.context import has_objective_backlog, is_mid_cycle
from desloppify.engine._plan.sync.dimensions import (
    current_unscored_ids,
    sync_subjective_dimensions,
)
from desloppify.engine._plan.sync.triage import (
    compute_new_issue_ids,
    is_triage_stale,
    sync_triage_needed,
)
from desloppify.engine._plan.sync.workflow import (
    ScoreSnapshot,
    import_scores_meta_matches,
    pending_import_scores_meta,
    sync_communicate_score_needed,
    sync_create_plan_needed,
    sync_import_scores_needed,
    sync_score_checkpoint_needed,
)

__all__ = [
    "AUTO_PREFIX",
    "QueueSyncResult",
    "ReconcileResult",
    "ReviewImportSyncResult",
    "SYNTHETIC_PREFIXES",
    "ScoreSnapshot",
    "WORKFLOW_COMMUNICATE_SCORE_ID",
    "WORKFLOW_CREATE_PLAN_ID",
    "WORKFLOW_DEFERRED_DISPOSITION_ID",
    "WORKFLOW_IDS",
    "WORKFLOW_IMPORT_SCORES_ID",
    "WORKFLOW_PREFIX",
    "WORKFLOW_RUN_SCAN_ID",
    "WORKFLOW_SCORE_CHECKPOINT_ID",
    "SubjectiveVisibility",
    "append_log_entry",
    "auto_cluster_issues",
    "clear_postflight_scan_completion",
    "confirmed_triage_stage_names",
    "compute_new_issue_ids",
    "compute_subjective_visibility",
    "current_lifecycle_phase",
    "current_unscored_ids",
    "has_living_plan",
    "has_objective_backlog",
    "import_scores_meta_matches",
    "is_mid_cycle",
    "is_triage_stale",
    "LIFECYCLE_PHASE_EXECUTE",
    "LIFECYCLE_PHASE_REVIEW",
    "LIFECYCLE_PHASE_SCAN",
    "LIFECYCLE_PHASE_TRIAGE",
    "LIFECYCLE_PHASE_WORKFLOW",
    "load_plan",
    "mark_postflight_scan_completed",
    "normalize_queue_workflow_and_triage_prefix",
    "NON_OBJECTIVE_DETECTORS",
    "open_review_ids",
    "pending_import_scores_meta",
    "postflight_scan_pending",
    "plan_path_for_state",
    "purge_ids",
    "reconcile_plan_after_scan",
    "resolve_lifecycle_phase",
    "review_issue_snapshot_hash",
    "recorded_unconfirmed_triage_stage_names",
    "save_plan",
    "set_lifecycle_phase",
    "sync_communicate_score_needed",
    "sync_create_plan_needed",
    "sync_import_scores_needed",
    "sync_lifecycle_phase",
    "sync_plan_after_review_import",
    "sync_score_checkpoint_needed",
    "sync_subjective_dimensions",
    "sync_triage_needed",
]
