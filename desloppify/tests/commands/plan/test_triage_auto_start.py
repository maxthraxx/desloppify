"""Tests for auto-start triage on --stage observe."""

from __future__ import annotations

import argparse

import desloppify.app.commands.plan.triage.command as triage_mod
from desloppify.app.commands.plan.triage import helpers as triage_helpers
from desloppify.app.commands.plan.triage.services import TriageServices
from desloppify.engine._plan.schema import empty_plan
from desloppify.engine._plan.constants import TRIAGE_IDS, TRIAGE_STAGE_IDS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state_with_issues(*ids: str, dimension: str = "naming") -> dict:
    issues = {}
    for fid in ids:
        issues[fid] = {
            "status": "open",
            "detector": "review",
            "file": "test.py",
            "summary": f"Review issue {fid}",
            "confidence": "medium",
            "tier": 2,
            "detail": {"dimension": dimension},
        }
    return {"issues": issues, "scan_count": 5, "dimension_scores": {}}


def _fake_runtime(state: dict):
    return type("Ctx", (), {"state": state, "config": {}})()


def _fake_args(**overrides) -> argparse.Namespace:
    defaults = {
        "lang": None,
        "path": ".",
        "confirm": None,
        "attestation": None,
        "confirmed": None,
        "stage": None,
        "report": None,
        "complete": False,
        "confirm_existing": False,
        "strategy": None,
        "note": None,
        "start": False,
        "dry_run": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _fake_services(plan, state, save_plan_fn=None):
    """Build a fake TriageServices with test stubs."""
    return TriageServices(
        command_runtime=lambda args: _fake_runtime(state),
        load_plan=lambda *a, **kw: plan,
        save_plan=save_plan_fn or (lambda p, *a, **kw: None),
        collect_triage_input=lambda p, s: type("TI", (), {
            "open_issues": s.get("issues", {}),
            "resolved_issues": {},
            "new_since_last": [],
            "resolved_since_last": [],
            "existing_clusters": {},
        })(),
        detect_recurring_patterns=lambda _a, _b: {},
        append_log_entry=lambda *a, **kw: None,
        extract_issue_citations=lambda text, ids: set(),
        build_triage_prompt=lambda si: "prompt",
    )


def _patch_triage(monkeypatch, plan, state, save_plan_fn=None):
    """Apply standard triage monkeypatches."""
    monkeypatch.setattr(
        triage_mod, "default_triage_services",
        lambda: _fake_services(plan, state, save_plan_fn),
    )
    monkeypatch.setattr(triage_mod, "require_issue_inventory", lambda s: True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutoStartTriage:
    def test_observe_auto_starts_triage(self, monkeypatch, capsys):
        """When no triage stages in queue, --stage observe auto-injects them."""
        plan = empty_plan()
        # No triage stage IDs in queue_order
        assert not any(sid in plan.get("queue_order", []) for sid in TRIAGE_IDS)

        state = _state_with_issues("r1", "r2", "r3", "r4", "r5")
        _patch_triage(monkeypatch, plan, state)

        long_report = (
            "This is a thorough analysis of the naming and architecture issues. "
            "The main themes are inconsistent naming conventions across modules, "
            "and some architectural coupling between components."
        )
        args = _fake_args(stage="observe", report=long_report)
        triage_mod.cmd_plan_triage(args)

        # All triage stage IDs should now be in queue
        assert all(sid in plan.get("queue_order", []) for sid in TRIAGE_STAGE_IDS)
        # Stage should be recorded
        stages = plan.get("epic_triage_meta", {}).get("triage_stages", {})
        assert "observe" in stages

    def test_observe_auto_start_prints_note(self, monkeypatch, capsys):
        """Auto-start prints a note about injecting triage::pending."""
        plan = empty_plan()
        state = _state_with_issues("r1", "r2", "r3", "r4", "r5")
        _patch_triage(monkeypatch, plan, state)

        long_report = (
            "This is a thorough analysis of the naming and architecture issues. "
            "The main themes are inconsistent naming conventions across modules, "
            "and some architectural coupling between components."
        )
        args = _fake_args(stage="observe", report=long_report)
        triage_mod.cmd_plan_triage(args)

        out = capsys.readouterr().out
        assert "auto-started" in out.lower()

    def test_observe_works_normally_when_already_started(self, monkeypatch, capsys):
        """When triage stages already in queue, observe proceeds without double-injection."""
        plan = empty_plan()
        plan["queue_order"] = list(TRIAGE_STAGE_IDS)

        state = _state_with_issues("r1", "r2", "r3", "r4", "r5")
        _patch_triage(monkeypatch, plan, state)

        long_report = (
            "This is a thorough analysis of the naming and architecture issues. "
            "The main themes are inconsistent naming conventions across modules, "
            "and some architectural coupling between components."
        )
        args = _fake_args(stage="observe", report=long_report)
        triage_mod.cmd_plan_triage(args)

        out = capsys.readouterr().out
        assert "auto-started" not in out.lower()
        # No duplicate stage IDs
        for sid in TRIAGE_STAGE_IDS:
            assert plan["queue_order"].count(sid) == 1
        # Stage should be recorded
        stages = plan.get("epic_triage_meta", {}).get("triage_stages", {})
        assert "observe" in stages

    def test_inject_triage_stages_prepends_and_deduplicates(self):
        """Inject helper keeps one copy of each stage ID at queue front."""
        plan = {
            "queue_order": [
                "review::a.py::x1",
                TRIAGE_STAGE_IDS[2],
                "review::b.py::x2",
                TRIAGE_STAGE_IDS[0],
                TRIAGE_STAGE_IDS[4],
            ]
        }

        triage_helpers.inject_triage_stages(plan)

        assert plan["queue_order"][: len(TRIAGE_STAGE_IDS)] == list(TRIAGE_STAGE_IDS)
        for sid in TRIAGE_STAGE_IDS:
            assert plan["queue_order"].count(sid) == 1
        assert plan["queue_order"][len(TRIAGE_STAGE_IDS) :] == [
            "review::a.py::x1",
            "review::b.py::x2",
        ]

    def test_inject_triage_stages_clears_skipped_entries(self):
        """Inject helper removes triage stage IDs from skipped."""
        plan = {
            "queue_order": ["review::a.py::x1"],
            "skipped": {
                "triage::enrich": {"kind": "temporary"},
                "triage::sense-check": {"kind": "temporary"},
                "review::z.py::x9": {"kind": "temporary"},
            },
        }

        triage_helpers.inject_triage_stages(plan)

        assert "triage::enrich" not in plan["skipped"]
        assert "triage::sense-check" not in plan["skipped"]
        assert "review::z.py::x9" in plan["skipped"]
