"""Post-import plan sync for review importing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from desloppify.app.commands.helpers.issue_id_display import short_issue_id
from desloppify.app.commands.review.importing.flags import imported_assessment_keys
from desloppify.base.config import target_strict_score_from_config
from desloppify.base.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.base.output.terminal import colorize
from desloppify.engine._plan.auto_cluster import auto_cluster_issues
from desloppify.engine._plan.constants import QueueSyncResult
from desloppify.engine._plan.operations.meta import append_log_entry
from desloppify.engine._plan.persistence import (
    has_living_plan,
    load_plan,
    plan_path_for_state,
    save_plan,
)
from desloppify.engine._plan.policy.subjective import compute_subjective_visibility
from desloppify.engine._plan.reconcile_review_import import (
    ReviewImportSyncResult,
    sync_plan_after_review_import,
)
from desloppify.engine._plan.refresh_lifecycle import sync_lifecycle_phase
from desloppify.engine._plan.sync.dimensions import sync_subjective_dimensions
from desloppify.engine._plan.sync.workflow_gates import (
    ScoreSnapshot,
    sync_communicate_score_needed,
    sync_create_plan_needed,
    sync_import_scores_needed,
)
from desloppify.engine.plan_triage import (
    TRIAGE_CMD_RUN_STAGES_CLAUDE,
    TRIAGE_CMD_RUN_STAGES_CODEX,
)
from desloppify.intelligence.review.importing.contracts_types import (
    NormalizedReviewImportPayload,
)
from desloppify.state_scoring import score_snapshot


@dataclass(frozen=True)
class PlanImportSyncRequest:
    """Bundle optional inputs for post-import plan synchronization."""

    state_file: str | Path | None = None
    config: dict | None = None
    import_file: str | None = None
    import_payload: NormalizedReviewImportPayload | None = None


@dataclass(frozen=True)
class _ImportSyncInputs:
    has_review_issue_delta: bool
    assessment_keys: frozenset[str]
    covered_ids: tuple[str, ...]


@dataclass
class _PlanImportMutations:
    dirty: bool = False
    workflow_injected_ids: list[str] = field(default_factory=list)
    import_result: ReviewImportSyncResult | None = None
    stale_sync_result: QueueSyncResult | None = None
    auto_cluster_changes: int = 0


def _has_postflight_review_work(state: dict, *, policy) -> bool:
    issues = state.get("issues", {})
    if any(
        isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("detector") in {"review", "concerns", "subjective_review"}
        for issue in issues.values()
    ):
        return True
    return bool(policy.stale_ids or policy.under_target_ids)


def _has_deferred_disposition_work(plan: dict) -> bool:
    skipped = plan.get("skipped", {})
    if not isinstance(skipped, dict):
        return False
    return any(
        isinstance(entry, dict) and str(entry.get("kind", "temporary")) == "temporary"
        for entry in skipped.values()
    )


def _sync_lifecycle_phase_after_import(plan: dict, state: dict, *, policy) -> bool:
    return sync_lifecycle_phase(
        plan,
        has_initial_reviews=bool(policy.unscored_ids),
        has_objective_backlog=bool(policy.has_objective_backlog),
        has_postflight_review=_has_postflight_review_work(state, policy=policy),
        has_postflight_workflow=any(
            item_id in plan.get("queue_order", [])
            for item_id in (
                "workflow::import-scores",
                "workflow::communicate-score",
                "workflow::score-checkpoint",
                "workflow::create-plan",
            )
        ),
        has_triage=any(
            isinstance(item_id, str) and item_id.startswith("triage::")
            for item_id in plan.get("queue_order", [])
        ),
        has_deferred=_has_deferred_disposition_work(plan),
    )[1]


def _print_review_import_sync(
    state: dict,
    result: ReviewImportSyncResult,
    *,
    workflow_injected: bool,
) -> None:
    """Print summary of plan changes after review import sync."""
    new_ids = result.new_ids
    stale_pruned = result.stale_pruned_from_queue
    print()
    _print_new_review_items(state, new_ids)
    _print_stale_review_prunes(stale_pruned)
    _print_review_import_footer(result, workflow_injected=workflow_injected)


def _print_new_review_items(state: dict, new_ids: list[str]) -> None:
    if not new_ids:
        return
    print(colorize(
        f"  Plan updated: {len(new_ids)} new review issue(s) added to queue.",
        "bold",
    ))
    issues = state.get("issues", {})
    for finding_id in sorted(new_ids)[:10]:
        finding = issues.get(finding_id, {})
        print(f"    * [{short_issue_id(finding_id)}] {finding.get('summary', '')}")
    if len(new_ids) > 10:
        print(colorize(f"    ... and {len(new_ids) - 10} more", "dim"))


def _print_stale_review_prunes(stale_pruned: list[str]) -> None:
    if not stale_pruned:
        return
    print(colorize(
        f"  Plan updated: {len(stale_pruned)} stale review issue(s) removed from queue.",
        "bold",
    ))


def _print_review_import_footer(
    result: ReviewImportSyncResult,
    *,
    workflow_injected: bool,
) -> None:
    print()
    print(colorize(
        "  Review queue sync completed. Workflow follow-up may be front-loaded.",
        "dim",
    ))
    print()
    print(colorize("  View execution queue:  desloppify plan queue", "dim"))
    print(colorize("  View newest first:     desloppify plan queue --sort recent", "dim"))
    print(colorize("  View broader backlog:  desloppify backlog", "dim"))
    print()
    print(colorize("  NEXT STEP:", "yellow"))
    print(colorize("    Run:    desloppify next", "yellow"))
    if result.triage_injected and not workflow_injected:
        print(colorize(f"    Codex:  {TRIAGE_CMD_RUN_STAGES_CODEX}", "dim"))
        print(colorize(f"    Claude: {TRIAGE_CMD_RUN_STAGES_CLAUDE}", "dim"))
        print(colorize("    Manual dashboard: desloppify plan triage", "dim"))
    print(colorize(
        "  (Follow the queue in order; score communication and planning come before triage.)",
        "dim",
    ))


def _review_delta_present(diff: dict) -> bool:
    return any(
        int(diff.get(key, 0) or 0) > 0
        for key in ("new", "reopened", "auto_resolved")
    )


def _print_workflow_injected_message(workflow_injected_ids: list[str]) -> None:
    if not workflow_injected_ids:
        return
    injected_parts = [f"`{workflow_id}`" for workflow_id in workflow_injected_ids]
    print(colorize(
        f"  Plan: {' and '.join(injected_parts)} queued. Run `desloppify next`.",
        "cyan",
    ))


def _build_import_sync_inputs(
    diff: dict,
    import_payload: NormalizedReviewImportPayload | None,
) -> _ImportSyncInputs:
    assessment_keys = (
        imported_assessment_keys(import_payload)
        if isinstance(import_payload, dict)
        else set()
    )
    return _ImportSyncInputs(
        has_review_issue_delta=_review_delta_present(diff),
        assessment_keys=frozenset(assessment_keys),
        covered_ids=tuple(
            f"subjective::{dim_key}"
            for dim_key in sorted(assessment_keys)
        ),
    )


def _record_workflow_change(
    mutations: _PlanImportMutations,
    result,
    *,
    workflow_id: str,
    injected: bool = True,
) -> None:
    if not result.changes:
        return
    mutations.dirty = True
    if injected:
        mutations.workflow_injected_ids.append(workflow_id)


def _sync_review_delta(
    mutations: _PlanImportMutations,
    plan: dict,
    state: dict,
    *,
    policy: SubjectiveVisibility,
    sync_inputs: _ImportSyncInputs,
) -> None:
    if not sync_inputs.has_review_issue_delta:
        return
    mutations.import_result = sync_plan_after_review_import(
        plan,
        state,
        policy=policy,
    )
    if mutations.import_result is not None:
        mutations.dirty = True


def _sync_subjective_queue_after_import(
    mutations: _PlanImportMutations,
    plan: dict,
    state: dict,
    *,
    policy: SubjectiveVisibility,
    target_strict: float,
    sync_inputs: _ImportSyncInputs,
) -> None:
    if not (sync_inputs.has_review_issue_delta or sync_inputs.assessment_keys):
        return
    cycle_just_completed = not plan.get("plan_start_scores")
    mutations.stale_sync_result = sync_subjective_dimensions(
        plan,
        state,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    )
    if mutations.stale_sync_result.changes:
        mutations.dirty = True

    mutations.auto_cluster_changes = int(auto_cluster_issues(
        plan,
        state,
        target_strict=target_strict,
        policy=policy,
    ))
    if mutations.auto_cluster_changes:
        mutations.dirty = True


def _append_workflow_log_entries(
    plan: dict,
    *,
    communicate_result,
    import_scores_result,
    create_plan_result,
) -> None:
    if communicate_result.changes:
        append_log_entry(
            plan,
            "sync_communicate_score",
            actor="system",
            detail={"trigger": "review_import", "injected": True},
        )
    if import_scores_result.changes:
        injected = bool(getattr(import_scores_result, "injected", ()))
        pruned = list(getattr(import_scores_result, "pruned", ()))
        append_log_entry(
            plan,
            "sync_import_scores",
            actor="system",
            detail={
                "trigger": "review_import",
                "injected": injected,
                "pruned": pruned,
            },
        )
    if create_plan_result.changes:
        append_log_entry(
            plan,
            "sync_create_plan",
            actor="system",
            detail={"trigger": "review_import", "injected": True},
        )


def _append_review_import_sync_log(
    plan: dict,
    diff: dict,
    mutations: _PlanImportMutations,
    *,
    covered_ids: tuple[str, ...],
) -> None:
    if not (
        mutations.import_result is not None
        or mutations.workflow_injected_ids
        or covered_ids
    ):
        return
    import_result = mutations.import_result
    stale_sync_result = mutations.stale_sync_result
    append_log_entry(
        plan,
        "review_import_sync",
        actor="system",
        detail={
            "trigger": "review_import",
            "new_ids": sorted(import_result.new_ids) if import_result is not None else [],
            "added_to_queue": (
                import_result.added_to_queue if import_result is not None else []
            ),
            "workflow_injected_ids": mutations.workflow_injected_ids,
            "triage_injected": (
                import_result.triage_injected if import_result is not None else False
            ),
            "triage_injected_ids": (
                import_result.triage_injected_ids if import_result is not None else []
            ),
            "triage_deferred": (
                import_result.triage_deferred if import_result is not None else False
            ),
            "diff_new": diff.get("new", 0),
            "diff_reopened": diff.get("reopened", 0),
            "diff_auto_resolved": diff.get("auto_resolved", 0),
            "stale_pruned_from_queue": (
                import_result.stale_pruned_from_queue if import_result is not None else []
            ),
            "covered_subjective": list(covered_ids),
            "stale_sync_injected": (
                sorted(stale_sync_result.injected)
                if stale_sync_result is not None else []
            ),
            "stale_sync_pruned": (
                sorted(stale_sync_result.pruned)
                if stale_sync_result is not None else []
            ),
            "auto_cluster_changes": mutations.auto_cluster_changes,
        },
    )


def sync_plan_after_import(
    state: dict,
    diff: dict,
    assessment_mode: str,
    *,
    request: PlanImportSyncRequest | None = None,
) -> None:
    """Apply issue/workflow syncs after import in one load/save cycle."""
    try:
        state_file = request.state_file if request is not None else None
        config = request.config if request is not None else None
        import_file = request.import_file if request is not None else None
        import_payload = request.import_payload if request is not None else None

        plan_path = None
        target_strict = target_strict_score_from_config(config)
        if state_file is not None:
            plan_path = plan_path_for_state(Path(state_file))
        if not has_living_plan(plan_path):
            return

        plan = load_plan(plan_path)
        policy = compute_subjective_visibility(
            state,
            target_strict=target_strict,
            plan=plan,
        )
        snapshot = score_snapshot(state)
        current_scores = ScoreSnapshot(
            strict=snapshot.strict,
            overall=snapshot.overall,
            objective=snapshot.objective,
            verified=snapshot.verified,
        )
        trusted_score_import = assessment_mode in {"trusted_internal", "attested_external"}
        communicate_result = sync_communicate_score_needed(
            plan,
            state,
            policy=policy,
            scores_just_imported=trusted_score_import,
            current_scores=current_scores,
        )
        import_scores_result = sync_import_scores_needed(
            plan,
            state,
            assessment_mode=assessment_mode,
            import_file=import_file,
            import_payload=import_payload,
        )
        create_plan_result = sync_create_plan_needed(
            plan,
            state,
            policy=policy,
        )

        sync_inputs = _build_import_sync_inputs(diff, import_payload)
        mutations = _PlanImportMutations()
        _record_workflow_change(
            mutations,
            communicate_result,
            workflow_id="workflow::communicate-score",
        )
        _record_workflow_change(
            mutations,
            import_scores_result,
            workflow_id="workflow::import-scores",
            injected=bool(getattr(import_scores_result, "injected", ())),
        )
        _record_workflow_change(
            mutations,
            create_plan_result,
            workflow_id="workflow::create-plan",
        )
        _sync_review_delta(
            mutations,
            plan,
            state,
            policy=policy,
            sync_inputs=sync_inputs,
        )
        _sync_subjective_queue_after_import(
            mutations,
            plan,
            state,
            policy=policy,
            target_strict=target_strict,
            sync_inputs=sync_inputs,
        )

        if mutations.dirty:
            if _sync_lifecycle_phase_after_import(plan, state, policy=policy):
                mutations.dirty = True
            _append_workflow_log_entries(
                plan,
                communicate_result=communicate_result,
                import_scores_result=import_scores_result,
                create_plan_result=create_plan_result,
            )
            _append_review_import_sync_log(
                plan,
                diff,
                mutations,
                covered_ids=sync_inputs.covered_ids,
            )
            save_plan(plan, plan_path)

        if mutations.import_result is not None:
            _print_review_import_sync(
                state,
                mutations.import_result,
                workflow_injected=bool(mutations.workflow_injected_ids),
            )
        _print_workflow_injected_message(mutations.workflow_injected_ids)
    except PLAN_LOAD_EXCEPTIONS as exc:
        print(
            colorize(
                f"  Note: skipped plan sync after review import ({exc}).",
                "dim",
            )
        )


__all__ = ["PlanImportSyncRequest", "sync_plan_after_import"]
