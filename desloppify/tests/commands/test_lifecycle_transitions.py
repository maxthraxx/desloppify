"""Integration tests for lifecycle transitions through reconcile → work queue.

Exercises the full lifecycle by walking through each stage:
  scan → initial reviews → communicate-score → create-plan → triage → objectives

Between scans, items are completed via ``purge_ids`` (what ``plan resolve``
does) and the queue is re-checked without reconciling.  ``reconcile`` only
runs at scan boundaries — matching the real CLI flow.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


import desloppify.app.commands.scan.plan_reconcile as reconcile_mod
from desloppify.base.subjective_dimensions import DISPLAY_NAMES
from desloppify.engine._plan.operations.lifecycle import purge_ids
from desloppify.engine._plan.schema import empty_plan
from desloppify.engine._plan.constants import (
    TRIAGE_STAGE_IDS,
    WORKFLOW_COMMUNICATE_SCORE_ID,
    WORKFLOW_CREATE_PLAN_ID,
)
from desloppify.engine._work_queue.core import (
    QueueBuildOptions,
    build_work_queue,
)

# ---------------------------------------------------------------------------
# Dimensions we use throughout (3 is enough to be readable)
# ---------------------------------------------------------------------------
DIM_KEYS = ("naming_quality", "logic_clarity", "type_safety")
DIM_DISPLAY = {k: DISPLAY_NAMES[k] for k in DIM_KEYS}

OBJECTIVE_ISSUES = [
    {"id": "obj-1", "detector": "complexity", "file": "src/app.py", "status": "open",
     "tier": 2, "confidence": "high"},
    {"id": "obj-2", "detector": "naming", "file": "src/util.py", "status": "open",
     "tier": 3, "confidence": "medium"},
]


# ---------------------------------------------------------------------------
# State / plan helpers
# ---------------------------------------------------------------------------

def _placeholder_dim_entries(key: str) -> tuple[str, dict, dict]:
    """(display_name, dim_scores_entry, subjective_assessment) for a placeholder."""
    return (
        DIM_DISPLAY[key],
        {
            "score": 0, "strict": 0, "failing": 0, "checks": 0,
            "detectors": {"subjective_assessment": {"placeholder": True, "dimension_key": key}},
        },
        {"score": 0, "placeholder": True},
    )


def _scored_dim_entries(key: str, score: float) -> tuple[str, dict, dict]:
    """(display_name, dim_scores_entry, subjective_assessment) with a real score."""
    return (
        DIM_DISPLAY[key],
        {
            "score": score, "strict": score, "failing": 0, "checks": 1,
            "detectors": {"subjective_assessment": {"placeholder": False, "dimension_key": key}},
        },
        {"score": score, "placeholder": False},
    )


def _build_state(
    issues: list[dict],
    dims: list[tuple[str, dict, dict]],
    *,
    strict_score: float | None = None,
    overall_score: float | None = None,
    objective_score: float | None = None,
) -> dict:
    state: dict = {
        "issues": {i["id"]: dict(i) for i in issues},
        "scan_count": 1,
        "dimension_scores": {},
        "subjective_assessments": {},
    }
    for display, dim_entry, assessment in dims:
        state["dimension_scores"][display] = dim_entry
        dim_key = dim_entry["detectors"]["subjective_assessment"]["dimension_key"]
        state["subjective_assessments"][dim_key] = assessment
    if strict_score is not None:
        state["strict_score"] = strict_score
    if overall_score is not None:
        state["overall_score"] = overall_score
    if objective_score is not None:
        state["objective_score"] = objective_score
    return state


def _reconcile(state: dict, plan: dict, monkeypatch) -> dict:
    """Simulate a scan boundary: run reconcile_plan_post_scan with mocked I/O."""
    saved: list[dict] = []
    monkeypatch.setattr(reconcile_mod, "load_plan", lambda _path=None: plan)
    monkeypatch.setattr(reconcile_mod, "save_plan", lambda p, _path=None: saved.append(p))
    reconcile_mod.reconcile_plan_post_scan(SimpleNamespace(
        state=state, state_path=Path("/tmp/fake-state.json"), config={},
    ))
    return saved[-1] if saved else plan


def _queue_ids(state: dict, plan: dict) -> list[str]:
    result = build_work_queue(state, options=QueueBuildOptions(plan=plan, count=None))
    return [item["id"] for item in result["items"]]


def _spoof_reviews_complete(state: dict) -> None:
    """Mutate state in place: replace all placeholder dims with scored ones."""
    for key in DIM_KEYS:
        display, dim_entry, assessment = _scored_dim_entries(key, 75.0)
        state["dimension_scores"][display] = dim_entry
        state["subjective_assessments"][key] = assessment
    state["strict_score"] = 75.0
    state["overall_score"] = 75.0
    state["objective_score"] = 80.0


def _complete_endgame_subjective_reruns(state: dict) -> None:
    """Mark scored subjective dimensions as fully current/high to clear rerun queue."""
    for key in DIM_KEYS:
        assessment = state["subjective_assessments"].setdefault(key, {})
        assessment["placeholder"] = False
        assessment["needs_review_refresh"] = False
        assessment["score"] = 100.0
        dim = state["dimension_scores"][DIM_DISPLAY[key]]
        dim["score"] = 100.0
        dim["strict"] = 100.0


def _add_review_issues(state: dict) -> None:
    """Mutate state in place: add review detector issues that trigger triage."""
    for key in ("naming_quality", "logic_clarity"):
        fid = f"review-{key}"
        state["issues"][fid] = {
            "id": fid, "detector": "review", "file": "src/app.py",
            "status": "open", "detail": {"dimension": key},
        }


# ---------------------------------------------------------------------------
# Test 1: Fresh scan — only initial reviews visible
# ---------------------------------------------------------------------------

class TestPhase1OnlyInitialReviews:

    def test_only_initial_reviews_visible(self, monkeypatch):
        """Fresh scan with placeholders: only subjective initial review items appear."""
        state = _build_state(OBJECTIVE_ISSUES, [_placeholder_dim_entries(k) for k in DIM_KEYS])
        plan = _reconcile(state, empty_plan(), monkeypatch)

        ids = _queue_ids(state, plan)

        assert all(fid.startswith("subjective::") for fid in ids)
        assert len(ids) == len(DIM_KEYS)
        assert "obj-1" not in ids
        assert "obj-2" not in ids


# ---------------------------------------------------------------------------
# Test 2: Complete reviews (between scans) → objectives unlock immediately
# ---------------------------------------------------------------------------

class TestCompleteReviewsUnlocksObjectives:

    def test_objectives_visible_after_completing_reviews(self, monkeypatch):
        """After completing reviews (no reconcile), lifecycle filter lets objectives through."""
        state = _build_state(OBJECTIVE_ISSUES, [_placeholder_dim_entries(k) for k in DIM_KEYS])
        plan = _reconcile(state, empty_plan(), monkeypatch)

        # Complete reviews: update state + purge subjective IDs
        subj_ids = [fid for fid in _queue_ids(state, plan) if fid.startswith("subjective::")]
        _spoof_reviews_complete(state)
        purge_ids(plan, subj_ids)

        # No reconcile — just check the queue
        ids = _queue_ids(state, plan)
        assert "obj-1" in ids
        assert "obj-2" in ids
        # communicate-score hasn't been injected yet (no scan since reviews)
        assert WORKFLOW_COMMUNICATE_SCORE_ID not in ids


# ---------------------------------------------------------------------------
# Test 3: Next scan after reviews → communicate-score + create-plan appear
# ---------------------------------------------------------------------------

class TestScanAfterReviewsInjectsWorkflow:

    def test_workflow_items_injected_on_next_scan(self, monkeypatch):
        """Scan after completing reviews injects communicate-score and create-plan.

        Workflow items are postflight — they only become visible after objectives
        are drained.
        """
        state = _build_state(OBJECTIVE_ISSUES, [_placeholder_dim_entries(k) for k in DIM_KEYS])
        plan = _reconcile(state, empty_plan(), monkeypatch)

        # Complete reviews between scans
        subj_ids = [fid for fid in _queue_ids(state, plan) if fid.startswith("subjective::")]
        _spoof_reviews_complete(state)
        purge_ids(plan, subj_ids)

        # --- Next scan (reconcile) ---
        plan = _reconcile(state, plan, monkeypatch)

        ids = _queue_ids(state, plan)
        # Objective items visible while they exist
        assert "obj-1" in ids
        # Workflow items are postflight — gated behind objective items
        assert WORKFLOW_COMMUNICATE_SCORE_ID not in ids

        # After completing objectives, postflight sequence begins.
        # Subjective reruns come before workflow items in the lifecycle.
        state["issues"]["obj-1"]["status"] = "fixed"
        state["issues"]["obj-2"]["status"] = "fixed"
        ids = _queue_ids(state, plan)
        # Subjective reruns visible first (stale assessments)
        assert any(fid.startswith("subjective::") for fid in ids), f"Expected subjective: {ids}"

        # After completing subjective reruns, workflow items become visible
        _complete_endgame_subjective_reruns(state)
        ids = _queue_ids(state, plan)
        assert WORKFLOW_COMMUNICATE_SCORE_ID in ids
        assert WORKFLOW_CREATE_PLAN_ID in ids
        assert ids.index(WORKFLOW_COMMUNICATE_SCORE_ID) < ids.index(WORKFLOW_CREATE_PLAN_ID)


# ---------------------------------------------------------------------------
# Phase-order contract: subjective -> score -> triage (after objective drains)
# ---------------------------------------------------------------------------

class TestPhaseOrderInvariant:

    def test_subjective_then_score_then_triage(self):
        """Endgame queue order is fixed once objective backlog is drained."""
        state = _build_state(
            [],
            [_scored_dim_entries("naming_quality", 80.0)],
            strict_score=76.2,
            overall_score=80.1,
            objective_score=99.9,
        )
        state["subjective_assessments"]["naming_quality"]["needs_review_refresh"] = True
        plan = empty_plan()
        plan["queue_order"] = [
            WORKFLOW_COMMUNICATE_SCORE_ID,
            "triage::observe",
            "subjective::naming_quality",
        ]
        # Mark postflight scan as done so it doesn't block
        plan["refresh_state"] = {"postflight_scan_completed_at_scan_count": 1}

        # Subjective reruns must block score + triage until completed.
        ids = _queue_ids(state, plan)
        assert ids == ["subjective::naming_quality"]

        # After subjective rerun completion, score workflow appears (triage gated behind it).
        state["subjective_assessments"]["naming_quality"]["needs_review_refresh"] = False
        state["subjective_assessments"]["naming_quality"]["score"] = 100.0
        state["dimension_scores"][DIM_DISPLAY["naming_quality"]]["score"] = 100.0
        state["dimension_scores"][DIM_DISPLAY["naming_quality"]]["strict"] = 100.0
        ids = _queue_ids(state, plan)
        assert WORKFLOW_COMMUNICATE_SCORE_ID in ids
        # Triage is gated behind workflow items in lifecycle
        assert "triage::observe" not in ids


# ---------------------------------------------------------------------------
# Test 4: Triage injected when review issues appear
# ---------------------------------------------------------------------------

class TestTriageInjectedOnScan:

    def test_triage_after_review_issues_on_scan(self, monkeypatch):
        """Scan with new review issues injects triage, but objectives stay unblocked."""
        state = _build_state(OBJECTIVE_ISSUES, [_placeholder_dim_entries(k) for k in DIM_KEYS])
        plan = _reconcile(state, empty_plan(), monkeypatch)

        # Complete reviews between scans
        subj_ids = [fid for fid in _queue_ids(state, plan) if fid.startswith("subjective::")]
        _spoof_reviews_complete(state)
        purge_ids(plan, subj_ids)

        # Add review issues + scan
        _add_review_issues(state)
        plan = _reconcile(state, plan, monkeypatch)

        ids = _queue_ids(state, plan)
        assert not any(fid.startswith("triage::") for fid in ids), ids
        assert "obj-1" in ids and "obj-2" in ids

        # Triage stages are still injected in plan order.
        assert all(sid in plan["queue_order"] for sid in TRIAGE_STAGE_IDS)

        # Once objective queue drains, lifecycle enters postflight.
        # Review issues are non-objective, so they surface in postflight phase.
        state["issues"]["obj-1"]["status"] = "fixed"
        state["issues"]["obj-2"]["status"] = "fixed"
        ids = _queue_ids(state, plan)
        # Non-objective review issues + subjective reruns visible
        non_obj_ids = [fid for fid in ids if fid.startswith("subjective::") or fid.startswith("review")]
        assert len(non_obj_ids) > 0, ids

        # After subjective reruns complete, review issues (non-objective) still visible.
        _complete_endgame_subjective_reruns(state)
        ids = _queue_ids(state, plan)
        review_ids = [fid for fid in ids if fid.startswith("review")]
        assert len(review_ids) > 0, f"Expected review issues: {ids}"

        # After resolving review issues, workflow items appear before triage.
        for fid in list(state["issues"]):
            if state["issues"][fid].get("detector") == "review":
                state["issues"][fid]["status"] = "fixed"
        ids = _queue_ids(state, plan)
        workflow_ids = [fid for fid in ids if fid.startswith("workflow::")]
        assert len(workflow_ids) > 0, f"Expected workflow items: {ids}"

        # After completing workflow items, triage becomes visible.
        purge_ids(plan, workflow_ids)
        ids = _queue_ids(state, plan)
        triage_ids = [fid for fid in ids if fid.startswith("triage::")]
        assert len(triage_ids) == len(TRIAGE_STAGE_IDS), ids


# ---------------------------------------------------------------------------
# Test 5: Full lifecycle golden path
# ---------------------------------------------------------------------------

class TestFullLifecycleGoldenPath:

    def test_golden_path(self, monkeypatch):
        """Walk through every lifecycle stage, completing items between scans."""
        state = _build_state(OBJECTIVE_ISSUES, [_placeholder_dim_entries(k) for k in DIM_KEYS])
        plan = empty_plan()

        # ── Scan 1: initial reviews ──
        plan = _reconcile(state, plan, monkeypatch)
        ids = _queue_ids(state, plan)
        assert all(fid.startswith("subjective::") for fid in ids), f"Scan 1: {ids}"
        assert len(ids) == len(DIM_KEYS)

        # ── Between scans: complete reviews ──
        _spoof_reviews_complete(state)
        purge_ids(plan, [fid for fid in ids if fid.startswith("subjective::")])

        ids = _queue_ids(state, plan)
        # Objectives unlocked immediately, no workflow items yet
        assert "obj-1" in ids, f"Post-reviews (no scan): {ids}"
        assert WORKFLOW_COMMUNICATE_SCORE_ID not in ids, f"Post-reviews (no scan): {ids}"

        # ── Scan 2: workflow items injected but gated behind objectives ──
        plan = _reconcile(state, plan, monkeypatch)
        ids = _queue_ids(state, plan)
        # Workflow items are postflight — only visible after objectives drain
        assert "obj-1" in ids, f"Scan 2: {ids}"
        assert WORKFLOW_COMMUNICATE_SCORE_ID not in ids, f"Scan 2: {ids}"

        # ── Between scans: complete objectives to unlock postflight ──
        state["issues"]["obj-1"]["status"] = "fixed"
        state["issues"]["obj-2"]["status"] = "fixed"
        ids = _queue_ids(state, plan)
        # Subjective reruns come first in postflight
        assert any(fid.startswith("subjective::") for fid in ids), f"Post-objectives: {ids}"

        # ── Complete subjective reruns to unlock workflow ──
        _complete_endgame_subjective_reruns(state)
        ids = _queue_ids(state, plan)
        assert WORKFLOW_COMMUNICATE_SCORE_ID in ids, f"Post-subjective: {ids}"
        assert WORKFLOW_CREATE_PLAN_ID in ids, f"Post-subjective: {ids}"

        # ── Complete workflow items ──
        purge_ids(plan, [WORKFLOW_COMMUNICATE_SCORE_ID, WORKFLOW_CREATE_PLAN_ID])
        ids = _queue_ids(state, plan)
        assert not any(fid.startswith("workflow::") for fid in ids), f"Post-workflow: {ids}"

        # ── Scan 3: add review issues + reopen objectives for mid-cycle test ──
        state["issues"]["obj-1"]["status"] = "open"
        state["issues"]["obj-2"]["status"] = "open"
        _add_review_issues(state)
        plan = _reconcile(state, plan, monkeypatch)
        ids = _queue_ids(state, plan)
        # Mid-cycle guard: triage NOT injected while objective work remains
        assert not any(fid.startswith("triage::") for fid in ids), f"Scan 3: {ids}"
        assert not any(sid in plan["queue_order"] for sid in TRIAGE_STAGE_IDS), (
            f"Triage should be deferred mid-cycle: {plan['queue_order']}"
        )
        assert plan["epic_triage_meta"].get("triage_recommended"), "triage_recommended flag expected"
        assert "obj-1" in ids and "obj-2" in ids, f"Scan 3: {ids}"

        # ── Complete objective queue → rescan injects triage in plan, but
        #    lifecycle shows subjective reruns first ──
        state["issues"]["obj-1"]["status"] = "fixed"
        state["issues"]["obj-2"]["status"] = "fixed"
        plan = _reconcile(state, plan, monkeypatch)
        ids = _queue_ids(state, plan)
        # Post-flight: non-objective items visible (subjective reruns + review issues)
        assert all(
            fid.startswith("subjective::") or fid.startswith("review")
            for fid in ids
        ), f"Subjective phase: {ids}"

        # ── Complete subjective reruns → review issues still visible ──
        _complete_endgame_subjective_reruns(state)
        ids = _queue_ids(state, plan)
        # Review issues (non-objective) block triage
        review_ids = [fid for fid in ids if fid.startswith("review")]
        assert len(review_ids) > 0, f"Expected review issues: {ids}"

        # ── Resolve review issues → workflow items appear ──
        for fid in list(state["issues"]):
            if state["issues"][fid].get("detector") == "review":
                state["issues"][fid]["status"] = "fixed"
        ids = _queue_ids(state, plan)
        workflow_ids = [fid for fid in ids if fid.startswith("workflow::")]
        assert len(workflow_ids) > 0, f"Expected workflow items: {ids}"

        # ── Complete workflow items → triage unlocks ──
        purge_ids(plan, workflow_ids)
        ids = _queue_ids(state, plan)
        triage_ids = [fid for fid in ids if fid.startswith("triage::")]
        assert len(triage_ids) == len(TRIAGE_STAGE_IDS), f"Triage unlock: {ids}"
        assert not plan["epic_triage_meta"].get("triage_recommended"), (
            "triage_recommended should be cleared after injection"
        )

        # ── Complete triage ──
        purge_ids(plan, list(TRIAGE_STAGE_IDS))
        ids = _queue_ids(state, plan)
        assert not any(fid.startswith("triage::") for fid in ids), f"Post-triage: {ids}"
