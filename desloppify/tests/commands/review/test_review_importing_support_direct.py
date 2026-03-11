"""Direct tests for review importing support modules."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import desloppify.app.commands.review.importing.cmd as import_cmd_mod
import desloppify.app.commands.review.importing.flags as flags_mod
import desloppify.app.commands.review.importing.plan_sync as plan_sync_mod
import desloppify.app.commands.review.importing.results as results_mod
import desloppify.engine.plan_queue as plan_queue_mod


def _empty_visibility_policy() -> SimpleNamespace:
    return SimpleNamespace(
        has_objective_backlog=False,
        unscored_ids=frozenset(),
        stale_ids=frozenset(),
        under_target_ids=frozenset(),
    )


def _score_snapshot(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _no_changes(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(changes=False, **kwargs)


def _patch_basic_plan_sync_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: dict,
    policy: SimpleNamespace | None = None,
) -> None:
    effective_policy = policy or _empty_visibility_policy()
    monkeypatch.setattr(plan_queue_mod, "has_living_plan", lambda _path=None: True)
    monkeypatch.setattr(plan_queue_mod, "load_plan", lambda _path=None: plan)
    monkeypatch.setattr(plan_queue_mod, "save_plan", lambda _plan, _path=None: None)
    monkeypatch.setattr(
        plan_queue_mod,
        "compute_subjective_visibility",
        lambda *_a, **_k: effective_policy,
    )
    monkeypatch.setattr(plan_queue_mod, "ScoreSnapshot", _score_snapshot)
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_communicate_score_needed",
        lambda _plan, _state, **_kwargs: _no_changes(),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_import_scores_needed",
        lambda _plan, _state, assessment_mode, **_kwargs: _no_changes(),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_create_plan_needed",
        lambda _plan, _state, policy=None: _no_changes(),
    )
    monkeypatch.setattr(plan_queue_mod, "append_log_entry", lambda *_a, **_k: None)


def test_plan_sync_uses_narrow_plan_facades() -> None:
    src = inspect.getsource(plan_sync_mod)
    assert "from desloppify.engine.plan import" not in src
    assert "desloppify.engine.plan_queue" in src


def test_flags_validation_and_assessment_state_helpers() -> None:
    with pytest.raises(flags_mod.ImportFlagValidationError):
        flags_mod.validate_import_flag_combos(
            attested_external=True,
            allow_partial=False,
            override_enabled=True,
            override_attest="ok",
        )
    with pytest.raises(flags_mod.ImportFlagValidationError):
        flags_mod.validate_import_flag_combos(
            attested_external=False,
            allow_partial=True,
            override_enabled=True,
            override_attest="ok",
        )

    keys = flags_mod.imported_assessment_keys(
        {"assessments": {"Naming Quality": 70, "": 50}}
    )
    assert keys == {"naming_quality"}

    state = {
        "scan_count": 4,
        "subjective_assessments": {"naming_quality": {"source": "holistic"}},
    }
    marked = flags_mod.mark_manual_override_assessments_provisional(
        state,
        assessment_keys={"naming_quality"},
    )
    assert marked == 1
    assert state["subjective_assessments"]["naming_quality"]["provisional_until_scan"] == 5

    cleared = flags_mod.clear_provisional_override_flags(
        state,
        assessment_keys={"naming_quality"},
    )
    assert cleared == 1
    assert state["subjective_assessments"]["naming_quality"]["source"] == "holistic"


def test_sync_plan_after_import_no_living_plan(monkeypatch) -> None:
    monkeypatch.setattr(plan_queue_mod, "has_living_plan", lambda _path=None: False)
    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
    )


def test_print_review_import_sync_reports_new_ids_and_triage_commands(capsys) -> None:
    state = {
        "issues": {
            "review::alpha": {"summary": "Alpha summary"},
            "review::beta": {"summary": "Beta summary"},
        }
    }
    result = SimpleNamespace(
        new_ids={"review::alpha", "review::beta"},
        stale_pruned_from_queue=["review::stale"],
        triage_injected=True,
    )

    plan_sync_mod._print_review_import_sync(
        state,
        result,
        workflow_injected=False,
    )

    out = capsys.readouterr().out
    assert "2 new review issue(s) added to queue" in out
    assert "Alpha summary" in out
    assert "stale review issue(s) removed from queue" in out
    assert plan_sync_mod.TRIAGE_CMD_RUN_STAGES_CODEX in out
    assert plan_sync_mod.TRIAGE_CMD_RUN_STAGES_CLAUDE in out


def test_sync_plan_after_import_scopes_living_plan_to_state_file(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    def fake_plan_path_for_state(state_path):
        seen["state_path"] = state_path
        return tmp_path / "plan.json"

    def fake_has_living_plan(path=None):
        seen["has_living_plan_path"] = path
        return False

    monkeypatch.setattr(plan_queue_mod, "plan_path_for_state", fake_plan_path_for_state)
    monkeypatch.setattr(plan_queue_mod, "has_living_plan", fake_has_living_plan)

    state_file = tmp_path / "state.json"
    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
        state_file=state_file,
    )

    assert seen["state_path"] == state_file
    assert seen["has_living_plan_path"] == tmp_path / "plan.json"


def test_sync_plan_after_import_handles_plan_exceptions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(plan_queue_mod, "has_living_plan", lambda _path=None: True)
    monkeypatch.setattr(
        plan_queue_mod,
        "load_plan",
        lambda _path=None: (_ for _ in ()).throw(OSError("boom")),
    )
    monkeypatch.setattr(plan_sync_mod, "PLAN_LOAD_EXCEPTIONS", (OSError,))

    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
    )
    out = capsys.readouterr().out
    assert "skipped plan sync after review import" in out


def test_sync_plan_after_import_runs_review_sync_for_auto_resolved_deltas(monkeypatch) -> None:
    plan: dict = {"queue_order": []}
    seen = {"import_called": False, "stale_called": False}

    _patch_basic_plan_sync_runtime(monkeypatch, plan=plan)
    monkeypatch.setattr(plan_queue_mod, "current_unscored_ids", lambda _state: set())
    monkeypatch.setattr(plan_queue_mod, "purge_ids", lambda _plan, _ids: None)

    def fake_import_sync(_plan, _state, policy=None):
        seen["import_called"] = True
        return None

    def fake_stale_sync(_plan, _state, policy=None, **_kw):
        seen["stale_called"] = True
        return SimpleNamespace(changes=False, injected=[], pruned=[])

    monkeypatch.setattr(plan_queue_mod, "sync_plan_after_review_import", fake_import_sync)
    monkeypatch.setattr(plan_queue_mod, "sync_subjective_dimensions", fake_stale_sync)

    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 0, "reopened": 0, "auto_resolved": 2},
        assessment_mode="issues_only",
    )

    assert seen["import_called"] is True
    assert seen["stale_called"] is True


def test_sync_plan_after_import_logs_triage_provenance(monkeypatch) -> None:
    plan: dict = {"queue_order": []}
    entries: list[tuple[str, dict]] = []

    _patch_basic_plan_sync_runtime(monkeypatch, plan=plan)
    monkeypatch.setattr(plan_queue_mod, "current_unscored_ids", lambda _state: set())
    monkeypatch.setattr(plan_queue_mod, "purge_ids", lambda _plan, _ids: None)
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_subjective_dimensions",
        lambda _plan, _state, policy=None, **_kw: SimpleNamespace(
            changes=False,
            injected=[],
            pruned=[],
        ),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_plan_after_review_import",
        lambda _plan, _state, policy=None: SimpleNamespace(
            new_ids={"review::x"},
            added_to_queue=["review::x"],
            triage_injected=True,
            stale_pruned_from_queue=[],
            triage_injected_ids=["triage::observe", "triage::reflect"],
            triage_deferred=False,
        ),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "append_log_entry",
        lambda _plan, action, **kwargs: entries.append((action, kwargs["detail"])),
    )

    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
    )

    assert entries
    action, detail = entries[-1]
    assert action == "review_import_sync"
    assert detail["triage_injected"] is True
    assert detail["triage_injected_ids"] == ["triage::observe", "triage::reflect"]
    assert detail["triage_deferred"] is False
    assert detail["stale_pruned_from_queue"] == []


def test_sync_plan_after_import_keeps_workflow_before_triage(monkeypatch) -> None:
    plan: dict = {
        "queue_order": [],
        "plan_start_scores": {"strict": 70.0, "overall": 70.0, "objective": 80.0, "verified": 80.0},
    }
    entries: list[tuple[str, dict]] = []

    monkeypatch.setattr(plan_queue_mod, "has_living_plan", lambda _path=None: True)
    monkeypatch.setattr(plan_queue_mod, "load_plan", lambda _path=None: plan)
    monkeypatch.setattr(plan_queue_mod, "save_plan", lambda _plan, _path=None: None)
    monkeypatch.setattr(plan_queue_mod, "current_unscored_ids", lambda _state: set())
    monkeypatch.setattr(plan_queue_mod, "purge_ids", lambda _plan, _ids: None)
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_subjective_dimensions",
        lambda _plan, _state, policy=None, **_kw: SimpleNamespace(
            changes=False,
            injected=[],
            pruned=[],
        ),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "ScoreSnapshot",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    def fake_communicate(_plan, _state, **_kwargs):
        _plan["queue_order"].append("workflow::communicate-score")
        plan_queue_mod.normalize_queue_workflow_and_triage_prefix(_plan["queue_order"])
        return SimpleNamespace(changes=True)

    def fake_create_plan(_plan, _state, policy=None):
        _plan["queue_order"].append("workflow::create-plan")
        plan_queue_mod.normalize_queue_workflow_and_triage_prefix(_plan["queue_order"])
        return SimpleNamespace(changes=True)

    def fake_review_import(_plan, _state, policy=None):
        _plan["queue_order"].extend(["review::x", "triage::observe"])
        return SimpleNamespace(
            new_ids={"review::x"},
            added_to_queue=["review::x"],
            triage_injected=True,
            stale_pruned_from_queue=[],
            triage_injected_ids=["triage::observe"],
            triage_deferred=False,
        )

    monkeypatch.setattr(plan_queue_mod, "sync_communicate_score_needed", fake_communicate)
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_import_scores_needed",
        lambda _plan, _state, assessment_mode, **_kwargs: SimpleNamespace(changes=False),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "compute_subjective_visibility",
        lambda *_a, **_k: SimpleNamespace(
            has_objective_backlog=False,
            unscored_ids=frozenset(),
            stale_ids=frozenset(),
            under_target_ids=frozenset(),
        ),
    )
    monkeypatch.setattr(plan_queue_mod, "sync_create_plan_needed", fake_create_plan)
    monkeypatch.setattr(plan_queue_mod, "sync_plan_after_review_import", fake_review_import)
    monkeypatch.setattr(
        plan_queue_mod,
        "append_log_entry",
        lambda _plan, action, **kwargs: entries.append((action, kwargs["detail"])),
    )

    plan_sync_mod.sync_plan_after_import(
        state={"issues": {"review::x": {"summary": "new review issue"}}},
        diff={"new": 1, "reopened": 0},
        assessment_mode="trusted_internal",
    )

    assert plan["queue_order"][:2] == [
        "workflow::communicate-score",
        "workflow::create-plan",
    ]
    assert plan["queue_order"].index("workflow::communicate-score") < plan["queue_order"].index("triage::observe")
    assert plan["queue_order"].index("workflow::create-plan") < plan["queue_order"].index("triage::observe")
    action, detail = entries[-1]
    assert action == "review_import_sync"
    assert detail["workflow_injected_ids"] == [
        "workflow::communicate-score",
        "workflow::create-plan",
    ]


def test_sync_plan_after_import_reuses_plan_aware_policy(monkeypatch) -> None:
    plan: dict = {"queue_order": []}
    policy = SimpleNamespace(
        has_objective_backlog=False,
        unscored_ids=frozenset(),
        stale_ids=frozenset(),
        under_target_ids=frozenset(),
    )
    seen: dict[str, object] = {}

    _patch_basic_plan_sync_runtime(monkeypatch, plan=plan, policy=policy)
    monkeypatch.setattr(plan_queue_mod, "current_unscored_ids", lambda _state: set())
    monkeypatch.setattr(plan_queue_mod, "purge_ids", lambda _plan, _ids: None)

    def fake_compute_policy(_state, *, target_strict, plan):
        seen["target_strict"] = target_strict
        seen["plan"] = plan
        return policy

    def fake_create_plan(_plan, _state, *, policy=None):
        seen["create_plan_policy"] = policy
        return SimpleNamespace(changes=False)

    def fake_review_import(_plan, _state, *, policy=None):
        seen["import_policy"] = policy
        return None

    def fake_stale_sync(_plan, _state, *, policy=None, **_kw):
        seen["stale_policy"] = policy
        return SimpleNamespace(changes=False, injected=[], pruned=[])

    monkeypatch.setattr(plan_queue_mod, "compute_subjective_visibility", fake_compute_policy)
    monkeypatch.setattr(plan_queue_mod, "sync_create_plan_needed", fake_create_plan)
    monkeypatch.setattr(plan_queue_mod, "sync_plan_after_review_import", fake_review_import)
    monkeypatch.setattr(plan_queue_mod, "sync_subjective_dimensions", fake_stale_sync)

    plan_sync_mod.sync_plan_after_import(
        state={},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
        config={"target_strict_score": 97},
    )

    assert seen["target_strict"] == 97.0
    assert seen["plan"] is plan
    assert seen["create_plan_policy"] is policy
    assert seen["import_policy"] is policy
    assert seen["stale_policy"] is policy


def test_sync_plan_after_import_does_not_purge_subjective_ids(monkeypatch) -> None:
    plan: dict = {"queue_order": ["subjective::naming_quality", "review::existing"]}
    purge_calls: list[list[str]] = []

    _patch_basic_plan_sync_runtime(monkeypatch, plan=plan)
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_plan_after_review_import",
        lambda _plan, _state, policy=None: SimpleNamespace(
            new_ids={"review::new"},
            added_to_queue=["review::new"],
            triage_injected=False,
            stale_pruned_from_queue=[],
            triage_injected_ids=[],
            triage_deferred=False,
        ),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "sync_subjective_dimensions",
        lambda _plan, _state, policy=None, **_kw: _no_changes(injected=[], pruned=[]),
    )
    monkeypatch.setattr(
        plan_queue_mod,
        "purge_ids",
        lambda _plan, ids: purge_calls.append(list(ids)),
    )

    plan_sync_mod.sync_plan_after_import(
        state={"issues": {"review::new": {"summary": "new review issue"}}},
        diff={"new": 1, "reopened": 0},
        assessment_mode="issues_only",
    )

    assert purge_calls == []
    assert "subjective::naming_quality" in plan["queue_order"]


def test_sync_plan_after_import_rebuilds_subjective_clusters_for_assessment_only_import(
    monkeypatch,
) -> None:
    plan: dict = {
        "queue_order": [
            "subjective::design_coherence",
            "subjective::naming_quality",
        ],
        "clusters": {
            "auto/initial-review": {
                "name": "auto/initial-review",
                "description": "Initial review of 2 unscored subjective dimensions",
                "issue_ids": [
                    "subjective::design_coherence",
                    "subjective::naming_quality",
                ],
                "created_at": "old",
                "updated_at": "old",
                "auto": True,
                "cluster_key": "subjective::unscored",
                "action": (
                    "desloppify review --prepare --dimensions "
                    "design_coherence,naming_quality"
                ),
                "user_modified": False,
            }
        },
        "overrides": {
            "subjective::design_coherence": {
                "issue_id": "subjective::design_coherence",
                "cluster": "auto/initial-review",
                "created_at": "old",
                "updated_at": "old",
            },
            "subjective::naming_quality": {
                "issue_id": "subjective::naming_quality",
                "cluster": "auto/initial-review",
                "created_at": "old",
                "updated_at": "old",
            },
        },
    }
    state = {
        "issues": {},
        "scan_count": 2,
        "dimension_scores": {
            "Design coherence": {
                "score": 80.0,
                "strict": 80.0,
                "checks": 1,
                "failing": 0,
                "detectors": {
                    "subjective_assessment": {
                        "dimension_key": "design_coherence",
                        "placeholder": False,
                    }
                },
            },
            "Naming quality": {
                "score": 78.0,
                "strict": 78.0,
                "checks": 1,
                "failing": 0,
                "detectors": {
                    "subjective_assessment": {
                        "dimension_key": "naming_quality",
                        "placeholder": False,
                    }
                },
            },
        },
        "subjective_assessments": {
            "design_coherence": {"score": 80.0, "needs_review_refresh": True},
            "naming_quality": {"score": 78.0, "needs_review_refresh": True},
        },
    }
    policy = SimpleNamespace(
        has_objective_backlog=False,
        objective_count=0,
        unscored_ids=frozenset(),
        stale_ids=frozenset(
            {"subjective::design_coherence", "subjective::naming_quality"}
        ),
        under_target_ids=frozenset(),
    )

    _patch_basic_plan_sync_runtime(monkeypatch, plan=plan, policy=policy)
    monkeypatch.setattr(
        plan_queue_mod,
        "append_log_entry",
        lambda *_a, **_k: None,
    )

    plan_sync_mod.sync_plan_after_import(
        state=state,
        diff={"new": 0, "reopened": 0, "auto_resolved": 0},
        assessment_mode="trusted_internal",
        import_payload={
            "assessments": {
                "Design coherence": 80,
                "Naming quality": 78,
            },
            "issues": [],
        },
    )

    assert "auto/initial-review" not in plan["clusters"]
    assert "auto/stale-review" in plan["clusters"]
    assert set(plan["clusters"]["auto/stale-review"]["issue_ids"]) == {
        "subjective::design_coherence",
        "subjective::naming_quality",
    }


def test_refresh_scorecard_after_import_only_for_trusted_assessments(monkeypatch) -> None:
    calls: list[tuple[object, dict, dict]] = []
    monkeypatch.setattr(
        import_cmd_mod,
        "emit_scorecard_badge",
        lambda args, config, state: (calls.append((args, config, state)), (None, None))[1],
    )

    trusted = SimpleNamespace(assessments_present=True, trusted=True)
    skipped = SimpleNamespace(assessments_present=True, trusted=False)
    scan_state = {
        "last_scan": "2026-03-10T00:00:00+00:00",
        "dimension_scores": {
            "Code quality": {
                "checks": 10,
                "score": 95.0,
                "strict": 95.0,
                "detectors": {"smells": {"potential": 10}},
            },
            "Naming quality": {
                "checks": 10,
                "score": 80.0,
                "strict": 80.0,
                "detectors": {
                    "subjective_assessment": {"dimension_key": "naming_quality"}
                },
            },
        },
    }

    assert import_cmd_mod._refresh_scorecard_after_import(
        state=scan_state,
        config={"badge_path": "scorecard.png"},
        assessment_policy=trusted,
    ) is True
    assert len(calls) == 1

    assert import_cmd_mod._refresh_scorecard_after_import(
        state=scan_state,
        config={"badge_path": "scorecard.png"},
        assessment_policy=skipped,
    ) is False
    assert len(calls) == 1

    assert import_cmd_mod._refresh_scorecard_after_import(
        state={"strict_score": 74.5},
        config={"badge_path": "scorecard.png"},
        assessment_policy=trusted,
    ) is False
    assert len(calls) == 1


def test_refresh_scorecard_after_import_skips_subjective_only_state(monkeypatch) -> None:
    calls: list[tuple[object, dict, dict]] = []
    monkeypatch.setattr(
        import_cmd_mod,
        "emit_scorecard_badge",
        lambda args, config, state: (calls.append((args, config, state)), (None, None))[1],
    )
    trusted = SimpleNamespace(assessments_present=True, trusted=True)
    subjective_only_state = {
        "last_scan": "2026-03-10T00:00:00+00:00",
        "dimension_scores": {
            "Naming quality": {
                "checks": 10,
                "score": 100.0,
                "strict": 100.0,
                "detectors": {
                    "subjective_assessment": {"dimension_key": "naming_quality"}
                },
            }
        },
    }

    assert import_cmd_mod._refresh_scorecard_after_import(
        state=subjective_only_state,
        config={"badge_path": "scorecard.png"},
        assessment_policy=trusted,
    ) is False
    assert calls == []


def test_print_import_results_writes_query_payload(monkeypatch) -> None:
    captured: list[dict] = []
    monkeypatch.setattr(results_mod.narrative_mod, "compute_narrative", lambda *_a, **_k: {"summary": "ok"})
    monkeypatch.setattr(results_mod.import_helpers_mod, "print_skipped_validation_details", lambda *_a, **_k: None)
    monkeypatch.setattr(results_mod.import_helpers_mod, "print_assessments_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(
        results_mod.import_helpers_mod,
        "print_open_review_summary",
        lambda *_a, **_k: "desloppify next",
    )
    monkeypatch.setattr(
        results_mod.import_helpers_mod,
        "print_review_import_scores_and_integrity",
        lambda *_a, **_k: [{"name": "Design coherence", "score": 95.0}],
    )
    monkeypatch.setattr(results_mod, "show_score_with_plan_context", lambda *_a, **_k: None)
    monkeypatch.setattr(results_mod, "write_query", lambda payload: captured.append(payload))

    results_mod.print_import_results(
        state={"issues": {}},
        lang_name="python",
        config={},
        diff={"new": 2, "auto_resolved": 1, "reopened": 0},
        prev=SimpleNamespace(overall=0),
        label="Holistic review",
        provisional_count=0,
        assessment_policy=SimpleNamespace(mode="issues_only", trusted=False, reason="untrusted"),
        scorecard_subjective_at_target_fn=lambda *_a, **_k: [],
    )

    assert captured
    payload = captured[0]
    assert payload["command"] == "review"
    assert payload["action"] == "import"
    assert payload["next_command"] == "desloppify next"
    assert payload["assessment_import"]["mode"] == "issues_only"


def test_print_import_results_reports_provisional_warning(capsys, monkeypatch) -> None:
    monkeypatch.setattr(results_mod.narrative_mod, "compute_narrative", lambda *_a, **_k: {})
    monkeypatch.setattr(results_mod.import_helpers_mod, "print_skipped_validation_details", lambda *_a, **_k: None)
    monkeypatch.setattr(results_mod.import_helpers_mod, "print_assessments_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(
        results_mod.import_helpers_mod,
        "print_open_review_summary",
        lambda *_a, **_k: "desloppify next",
    )
    monkeypatch.setattr(
        results_mod.import_helpers_mod,
        "print_review_import_scores_and_integrity",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(results_mod, "show_score_with_plan_context", lambda *_a, **_k: None)
    monkeypatch.setattr(results_mod, "write_query", lambda _payload: None)

    results_mod.print_import_results(
        state={"issues": {}},
        lang_name="python",
        config={},
        diff={"new": 1, "auto_resolved": 0, "reopened": 0},
        prev=SimpleNamespace(overall=0),
        label="Holistic review",
        provisional_count=2,
        assessment_policy=SimpleNamespace(mode="manual_override", trusted=False, reason="manual"),
        scorecard_subjective_at_target_fn=lambda *_a, **_k: [],
    )

    out = capsys.readouterr().out
    assert "manual override assessments are provisional" in out


def test_plan_sync_source_preserves_scoped_sync_pipeline_contract() -> None:
    src = inspect.getsource(plan_sync_mod.sync_plan_after_import)
    assert "plan_path = None" in src
    assert "plan_queue_mod.plan_path_for_state(Path(state_file))" in src
    assert "if not plan_queue_mod.has_living_plan(plan_path):" in src
    assert "plan = plan_queue_mod.load_plan(plan_path)" in src
    assert "policy = plan_queue_mod.compute_subjective_visibility(" in src
    assert "snapshot = state_mod.score_snapshot(state)" in src
    assert "current_scores = plan_queue_mod.ScoreSnapshot(" in src
    assert 'trusted_score_import = assessment_mode in {"trusted_internal", "attested_external"}' in src
    assert "communicate_result = plan_queue_mod.sync_communicate_score_needed(" in src
    assert "import_scores_result = plan_queue_mod.sync_import_scores_needed(" in src
    assert "create_plan_result = plan_queue_mod.sync_create_plan_needed(" in src
    assert "import_result = plan_queue_mod.sync_plan_after_review_import(" in src
    assert "stale_sync_result = plan_queue_mod.sync_subjective_dimensions(" in src
    assert 'workflow_injected_ids.append("workflow::communicate-score")' in src
    assert 'workflow_injected_ids.append("workflow::import-scores")' in src
    assert 'workflow_injected_ids.append("workflow::create-plan")' in src
    assert 'plan_queue_mod.append_log_entry(' in src
    assert '"review_import_sync"' in src
    assert "plan_queue_mod.save_plan(plan, plan_path)" in src
    assert "_print_review_import_sync(" in src


def test_results_source_preserves_query_and_narrative_contract() -> None:
    src = inspect.getsource(results_mod.print_import_results)
    assert "narrative_mod.compute_narrative(" in src
    assert 'NarrativeContext(lang=lang_name, command="review")' in src
    assert 'print(colorize(f"\\n  {label} imported:", "bold"))' in src
    assert 'issue_count = int(diff.get("new", 0) or 0)' in src
    assert "import_helpers_mod.print_skipped_validation_details" in src
    assert "import_helpers_mod.print_assessments_summary" in src
    assert "next_command = import_helpers_mod.print_open_review_summary(" in src
    assert "show_score_with_plan_context(state, prev)" in src
    assert "import_helpers_mod.print_review_import_scores_and_integrity(" in src
    assert 'f"  Next command to improve subjective scores: `{next_command}`"' in src
    assert "write_query(" in src
    assert '"command": "review"' in src
    assert '"action": "import"' in src
    assert '"mode": "holistic"' in src
    assert '"diff": diff' in src
    assert '"next_command": next_command' in src
    assert '"subjective_at_target": [' in src
    assert '"assessment_import": {' in src
    assert '"narrative": narrative' in src
