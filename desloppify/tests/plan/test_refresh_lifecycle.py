from __future__ import annotations

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
    sync_lifecycle_phase,
)
from desloppify.engine._plan.schema import empty_plan


def test_postflight_scan_pending_until_completed() -> None:
    plan = empty_plan()

    assert postflight_scan_pending(plan) is True

    changed = mark_postflight_scan_completed(plan, scan_count=7)

    assert changed is True
    assert postflight_scan_pending(plan) is False
    assert plan["refresh_state"]["postflight_scan_completed_at_scan_count"] == 7


def test_clearing_completion_ignores_synthetic_ids() -> None:
    plan = empty_plan()
    mark_postflight_scan_completed(plan, scan_count=3)

    changed = clear_postflight_scan_completion(
        plan,
        issue_ids=["workflow::run-scan", "triage::observe", "subjective::naming_quality"],
    )

    assert changed is False
    assert postflight_scan_pending(plan) is False


def test_clearing_completion_for_real_issue_requires_new_scan() -> None:
    plan = empty_plan()
    mark_postflight_scan_completed(plan, scan_count=5)

    changed = clear_postflight_scan_completion(
        plan,
        issue_ids=["unused::src/app.ts::thing"],
    )

    assert changed is True
    assert postflight_scan_pending(plan) is True
    assert current_lifecycle_phase(plan) == LIFECYCLE_PHASE_EXECUTE


def test_current_lifecycle_phase_falls_back_for_legacy_plans() -> None:
    plan = empty_plan()
    assert current_lifecycle_phase(plan) == LIFECYCLE_PHASE_SCAN

    mark_postflight_scan_completed(plan, scan_count=2)
    plan["plan_start_scores"] = {"strict": 75.0}
    assert current_lifecycle_phase(plan) == LIFECYCLE_PHASE_EXECUTE


def test_sync_lifecycle_phase_persists_explicit_phase_order() -> None:
    plan = empty_plan()

    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=True,
        has_objective_backlog=False,
        has_postflight_review=False,
        has_postflight_workflow=False,
        has_triage=False,
        has_deferred=False,
    )
    assert changed is True
    assert phase == LIFECYCLE_PHASE_REVIEW
    assert current_lifecycle_phase(plan) == LIFECYCLE_PHASE_REVIEW

    mark_postflight_scan_completed(plan, scan_count=5)
    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=False,
        has_objective_backlog=False,
        has_postflight_review=False,
        has_postflight_workflow=True,
        has_triage=False,
        has_deferred=False,
    )
    assert changed is True
    assert phase == LIFECYCLE_PHASE_WORKFLOW

    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=False,
        has_objective_backlog=False,
        has_postflight_review=False,
        has_postflight_workflow=False,
        has_triage=True,
        has_deferred=False,
    )
    assert changed is True
    assert phase == LIFECYCLE_PHASE_TRIAGE

    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=False,
        has_objective_backlog=True,
        has_postflight_review=False,
        has_postflight_workflow=False,
        has_triage=False,
        has_deferred=False,
    )
    assert changed is True
    assert phase == LIFECYCLE_PHASE_EXECUTE

    plan["refresh_state"].pop("postflight_scan_completed_at_scan_count", None)
    phase, changed = sync_lifecycle_phase(
        plan,
        has_initial_reviews=False,
        has_objective_backlog=False,
        has_postflight_review=False,
        has_postflight_workflow=False,
        has_triage=False,
        has_deferred=False,
    )
    assert changed is True
    assert phase == LIFECYCLE_PHASE_SCAN
