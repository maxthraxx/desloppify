"""Post-scan plan reconciliation — sync plan queue metadata after a scan merge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from desloppify.app.commands.scan.workflow import ScanRuntime

from desloppify import state as state_mod
from desloppify.base.config import DEFAULT_TARGET_STRICT_SCORE
from desloppify.base.exception_sets import PLAN_LOAD_EXCEPTIONS
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.output.terminal import colorize
from desloppify.engine.plan_queue import (
    LIFECYCLE_PHASE_SCAN,
    SYNTHETIC_PREFIXES,
    ScoreSnapshot,
    WORKFLOW_COMMUNICATE_SCORE_ID,
    append_log_entry,
    auto_cluster_issues,
    compute_subjective_visibility,
    is_mid_cycle,
    load_plan,
    mark_postflight_scan_completed,
    reconcile_plan_after_scan,
    save_plan,
    sync_lifecycle_phase,
    sync_communicate_score_needed,
    sync_create_plan_needed,
    sync_subjective_dimensions,
    sync_triage_needed,
)
from desloppify.engine.work_queue import build_deferred_disposition_item

logger = logging.getLogger(__name__)


def _reset_cycle_for_force_rescan(plan: dict[str, object]) -> bool:
    """Clear all cycle state when --force-rescan is used.

    Force-rescan means "start over" — the old cycle's triage stages,
    workflow items, subjective dimensions, plan-start scores, and triage
    metadata are all stale and must be removed.
    """
    order: list[str] = plan.get("queue_order", [])
    synthetic = [item for item in order if any(item.startswith(p) for p in SYNTHETIC_PREFIXES)]
    if not synthetic and not plan.get("plan_start_scores"):
        return False
    for item in synthetic:
        order.remove(item)
    plan["plan_start_scores"] = {}
    plan.pop("previous_plan_start_scores", None)
    plan.pop("scan_count_at_plan_start", None)
    meta = plan.get("epic_triage_meta", {})
    if isinstance(meta, dict):
        meta.pop("triage_recommended", None)
    count = len(synthetic)
    if count:
        print(
            colorize(
                f"  Plan: force-rescan — removed {count} synthetic item(s) "
                f"and reset cycle state.",
                "yellow",
            )
        )
    return True


def _plan_has_user_content(plan: dict[str, object]) -> bool:
    """Return True when the living plan has any user-managed queue metadata."""
    return bool(
        plan.get("queue_order")
        or plan.get("overrides")
        or plan.get("clusters")
        or plan.get("skipped")
    )


def _apply_plan_reconciliation(plan: dict[str, object], state: state_mod.StateModel, reconcile_fn) -> bool:
    """Apply standard post-scan plan reconciliation when user content exists."""
    if not _plan_has_user_content(plan):
        return False
    recon = reconcile_fn(plan, state)
    if recon.resurfaced:
        print(
            colorize(
                f"  Plan: {len(recon.resurfaced)} skipped item(s) re-surfaced after review period.",
                "cyan",
            )
        )
    return bool(recon.changes)


def _sync_subjective_dimensions_display(plan: dict[str, object], state: state_mod.StateModel, sync_fn) -> bool:
    """Sync all subjective dimensions (unscored + stale + under-target) in plan queue."""
    sync = sync_fn(plan, state)
    if sync.resurfaced:
        print(
            colorize(
                f"  Plan: {len(sync.resurfaced)} skipped subjective dimension(s) resurfaced — never reviewed.",
                "yellow",
            )
        )
    if sync.pruned:
        print(
            colorize(
                f"  Plan: {len(sync.pruned)} refreshed subjective dimension(s) removed from queue.",
                "cyan",
            )
        )
    if sync.injected:
        print(
            colorize(
                f"  Plan: {len(sync.injected)} subjective dimension(s) queued for review.",
                "cyan",
            )
        )
    return bool(sync.changes)


def _sync_auto_clusters(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    target_strict: float = DEFAULT_TARGET_STRICT_SCORE,
    policy=None,
) -> bool:
    """Regenerate automatic task clusters after scan merge."""
    return bool(auto_cluster_issues(
        plan, state,
        target_strict=target_strict,
        policy=policy,
    ))


def _seed_plan_start_scores(plan: dict[str, object], state: state_mod.StateModel) -> bool:
    """Set plan_start_scores when beginning a new queue cycle."""
    existing = plan.get("plan_start_scores")
    if existing and not isinstance(existing, dict):
        return False
    # Seed when empty OR when it's the reset sentinel ({"reset": True})
    if existing and not existing.get("reset"):
        return False
    scores = state_mod.score_snapshot(state)
    if scores.strict is None:
        return False
    plan["plan_start_scores"] = {
        "strict": scores.strict,
        "overall": scores.overall,
        "objective": scores.objective,
        "verified": scores.verified,
    }
    # New cycle — clear the communicate-score sentinel so it can fire again.
    plan.pop("previous_plan_start_scores", None)
    # Record scan count at cycle start so gates can detect whether a new scan ran
    plan["scan_count_at_plan_start"] = int(state.get("scan_count", 0) or 0)
    return True


def _has_objective_cycle(
    state: state_mod.StateModel,
    plan: dict[str, object],
) -> bool | None:
    """Return True when objective queue work exists and a cycle baseline should freeze."""
    try:
        from desloppify.app.commands.helpers.queue_progress import (
            plan_aware_queue_breakdown,
        )

        breakdown = plan_aware_queue_breakdown(state, plan)
    except PLAN_LOAD_EXCEPTIONS as exc:
        log_best_effort_failure(logger, "compute queue breakdown for plan-start seeding", exc)
        return None
    return breakdown.objective_actionable > 0


def _clear_plan_start_scores_if_queue_empty(
    state: state_mod.StateModel, plan: dict[str, object]
) -> bool:
    """Clear plan-start score snapshot once the queue is fully drained."""
    if not plan.get("plan_start_scores"):
        return False
    # Don't clear while communicate-score is pending — the rebaseline just
    # set plan_start_scores and the user hasn't seen the update yet.
    if WORKFLOW_COMMUNICATE_SCORE_ID in plan.get("queue_order", []):
        return False

    try:
        from desloppify.app.commands.helpers.queue_progress import (
            ScoreDisplayMode,
            plan_aware_queue_breakdown,
            score_display_mode,
        )

        breakdown = plan_aware_queue_breakdown(state, plan)
        frozen_strict = plan.get("plan_start_scores", {}).get("strict")
        queue_empty = score_display_mode(breakdown, frozen_strict) is not ScoreDisplayMode.FROZEN
    except PLAN_LOAD_EXCEPTIONS as exc:
        log_best_effort_failure(logger, "run post-scan plan reconciliation", exc)
        return False
    if not queue_empty:
        return False
    state["_plan_start_scores_for_reveal"] = dict(plan["plan_start_scores"])
    plan["plan_start_scores"] = {}
    # Clear the cycle sentinel so communicate-score can be injected
    # in the next cycle.
    plan.pop("previous_plan_start_scores", None)
    return True


def _mark_postflight_scan_completed_if_ready(
    state: state_mod.StateModel,
    plan: dict[str, object],
) -> bool:
    """Record that the scan stage completed for the current empty-queue boundary."""
    if build_deferred_disposition_item(plan) is not None:
        return False
    objective_cycle = _has_objective_cycle(state, plan)
    if objective_cycle is not False:
        return False
    return mark_postflight_scan_completed(
        plan,
        scan_count=int(state.get("scan_count", 0) or 0),
    )


def _subjective_policy_context(
    runtime: ScanRuntime,
    plan: dict[str, object],
) -> tuple[float, object, bool]:
    from desloppify.base.config import target_strict_score_from_config

    target_strict = target_strict_score_from_config(runtime.config)
    policy = compute_subjective_visibility(
        runtime.state,
        target_strict=target_strict,
        plan=plan,
    )
    cycle_just_completed = not plan.get("plan_start_scores")
    return target_strict, policy, cycle_just_completed


def _sync_subjective_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
    cycle_just_completed: bool,
) -> bool:
    changed = _sync_subjective_dimensions_display(
        plan,
        state,
        lambda p, s: sync_subjective_dimensions(
            p,
            s,
            policy=policy,
            cycle_just_completed=cycle_just_completed,
        ),
    )
    if changed:
        append_log_entry(plan, "sync_subjective", actor="system", detail={"changes": True})
    return changed


def _sync_auto_clusters_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    target_strict: float,
    policy,
) -> bool:
    changed = _sync_auto_clusters(
        plan,
        state,
        target_strict=target_strict,
        policy=policy,
    )
    if changed:
        append_log_entry(plan, "auto_cluster", actor="system", detail={"changes": True})
    return changed


def _sync_triage_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy=None,
) -> bool:
    triage_sync = sync_triage_needed(plan, state, policy=policy)
    if triage_sync.deferred:
        meta = plan.get("epic_triage_meta", {})
        if meta.get("triage_recommended"):
            print(
                colorize(
                    "  Plan: review issues changed — triage recommended after current work.",
                    "dim",
                )
            )
        return False
    if not triage_sync.changes:
        return False
    if triage_sync.injected:
        print(
            colorize(
                "  Plan: planning mode needed — review issues changed since last triage.",
                "cyan",
            )
        )
        append_log_entry(plan, "sync_triage", actor="system", detail={"injected": True})
    return True


def _sync_communicate_score_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    snapshot = state_mod.score_snapshot(state)
    current_scores = ScoreSnapshot(
        strict=snapshot.strict,
        overall=snapshot.overall,
        objective=snapshot.objective,
        verified=snapshot.verified,
    )
    communicate_sync = sync_communicate_score_needed(
        plan, state, policy=policy, current_scores=current_scores,
    )
    if not communicate_sync.changes:
        return False
    append_log_entry(
        plan,
        "sync_communicate_score",
        actor="system",
        detail={"injected": True},
    )
    return True


def _sync_create_plan_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    create_plan_sync = sync_create_plan_needed(plan, state, policy=policy)
    if not create_plan_sync.changes:
        return False
    if create_plan_sync.injected:
        print(
            colorize(
                "  Plan: reviews complete — `workflow::create-plan` queued.",
                "cyan",
            )
        )
        append_log_entry(plan, "sync_create_plan", actor="system", detail={"injected": True})
    return True


def _sync_plan_start_scores_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    seeded = _seed_plan_start_scores(plan, state)
    if seeded:
        append_log_entry(plan, "seed_start_scores", actor="system", detail={})
        return True
    # Only clear scores that existed before this reconcile pass —
    # never clear scores we just seeded in the same scan.
    cleared = _clear_plan_start_scores_if_queue_empty(state, plan)
    if cleared:
        append_log_entry(plan, "clear_start_scores", actor="system", detail={})
    return cleared


def _sync_postflight_scan_completion_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    changed = _mark_postflight_scan_completed_if_ready(state, plan)
    if changed:
        append_log_entry(
            plan,
            "complete_postflight_scan",
            actor="system",
            detail={"scan_count": int(state.get("scan_count", 0) or 0)},
        )
    return changed


def _has_postflight_review_work(
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    issues = state.get("issues", {})
    has_review_like_issue = any(
        isinstance(issue, dict)
        and issue.get("status") == "open"
        and issue.get("detector") in {"review", "concerns", "subjective_review"}
        for issue in issues.values()
    )
    if has_review_like_issue:
        return True
    return bool(policy.stale_ids or policy.under_target_ids)


def _has_postflight_workflow_items(plan: dict[str, object]) -> bool:
    order = plan.get("queue_order", [])
    return any(
        item_id in order
        for item_id in (
            "workflow::import-scores",
            "workflow::communicate-score",
            "workflow::score-checkpoint",
            "workflow::create-plan",
        )
    )


def _has_triage_items(plan: dict[str, object]) -> bool:
    return any(
        isinstance(item_id, str) and item_id.startswith("triage::")
        for item_id in plan.get("queue_order", [])
    )


def _sync_lifecycle_phase_and_log(
    plan: dict[str, object],
    state: state_mod.StateModel,
    *,
    policy,
) -> bool:
    has_deferred = build_deferred_disposition_item(plan) is not None
    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=bool(policy.unscored_ids),
        has_objective_backlog=bool(policy.has_objective_backlog),
        has_postflight_review=_has_postflight_review_work(state, policy=policy),
        has_postflight_workflow=_has_postflight_workflow_items(plan),
        has_triage=_has_triage_items(plan),
        has_deferred=has_deferred,
    )
    if changed:
        append_log_entry(
            plan,
            "sync_lifecycle_phase",
            actor="system",
            detail={"phase": phase},
        )
    return changed


def _sync_post_scan_without_policy(
    *,
    plan: dict[str, object],
    state: state_mod.StateModel,
) -> bool:
    """Run post-scan sync steps that do not require subjective policy context."""
    return bool(_apply_plan_reconciliation(plan, state, reconcile_plan_after_scan))


def _is_mid_cycle_scan(plan: dict[str, object], state: state_mod.StateModel) -> bool:
    """Return True when a plan cycle is active and queue items remain.

    Extends ``is_mid_cycle`` (which checks ``plan_start_scores``) with an
    additional queue-items guard — even if a cycle is nominally active, we
    only skip destructive operations when work actually remains.

    Mid-cycle scans (via --force-rescan or PHASE_TRANSITION gate) must NOT
    regenerate clusters or inject triage stages — doing so wipes triage
    state and reorders the queue, undoing prioritisation work.
    """
    if not is_mid_cycle(plan):
        return False
    order = plan.get("queue_order", [])
    skipped = plan.get("skipped", {})
    return any(item not in skipped for item in order)


def _sync_post_scan_with_policy(
    *,
    plan: dict[str, object],
    state: state_mod.StateModel,
    target_strict: float,
    policy,
    cycle_just_completed: bool,
    force_rescan: bool = False,
) -> bool:
    """Run post-scan sync steps that require policy/cycle context.

    When running mid-cycle (plan_start_scores set, queue non-empty) or
    via --force-rescan, skip auto-clustering and triage injection.  These
    steps regenerate queue structure and issue IDs, which wipes triage
    state and reorders the queue.  They only run at cycle boundaries
    (pre-flight / post-flight).
    """
    dirty = False
    mid_cycle = _is_mid_cycle_scan(plan, state) or force_rescan

    if _sync_subjective_and_log(
        plan,
        state,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
    ):
        dirty = True
    if not mid_cycle:
        if _sync_auto_clusters_and_log(
            plan,
            state,
            target_strict=target_strict,
            policy=policy,
        ):
            dirty = True
    else:
        print(
            colorize(
                "  Plan: mid-cycle scan — skipping cluster regeneration to "
                "preserve queue state.",
                "dim",
            )
        )
    if _sync_communicate_score_and_log(plan, state, policy=policy):
        dirty = True
    if _sync_create_plan_and_log(plan, state, policy=policy):
        dirty = True
    if _sync_triage_and_log(plan, state, policy=policy):
        dirty = True
    if not force_rescan:
        if _sync_plan_start_scores_and_log(plan, state):
            dirty = True
        if _sync_postflight_scan_completion_and_log(plan, state):
            dirty = True
    if _sync_lifecycle_phase_and_log(plan, state, policy=policy):
        dirty = True
    return dirty


def reconcile_plan_post_scan(runtime: ScanRuntime) -> None:
    """Reconcile plan queue metadata and stale subjective review dimensions."""
    plan_path = runtime.state_path.parent / "plan.json" if runtime.state_path else None
    try:
        plan = load_plan(plan_path)
    except PLAN_LOAD_EXCEPTIONS as exc:
        logger.warning("Plan reconciliation skipped (load failed): %s", exc)
        return

    force_rescan = getattr(runtime, "force_rescan", False)

    # Force-rescan: clear all cycle state first.  The user explicitly chose
    # to start over, so triage stages, workflow items, subjective dimensions,
    # and plan-start scores from the old cycle are all stale.
    dirty = _reset_cycle_for_force_rescan(plan) if force_rescan else False

    dirty = _sync_post_scan_without_policy(plan=plan, state=runtime.state) or dirty

    # Policy must be computed after reconciliation, which mutates plan
    # (supersede/prune resolved issues) before policy reads it.
    target_strict, policy, cycle_just_completed = _subjective_policy_context(
        runtime,
        plan,
    )
    dirty = _sync_post_scan_with_policy(
        plan=plan,
        state=runtime.state,
        target_strict=target_strict,
        policy=policy,
        cycle_just_completed=cycle_just_completed,
        force_rescan=getattr(runtime, "force_rescan", False),
    ) or dirty

    if dirty:
        try:
            save_plan(plan, plan_path)
        except PLAN_LOAD_EXCEPTIONS as exc:
            logger.warning("Plan reconciliation save failed: %s", exc)
