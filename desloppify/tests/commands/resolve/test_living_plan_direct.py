"""Direct tests for resolve living-plan helpers."""

from __future__ import annotations

import argparse

import desloppify.app.commands.resolve.living_plan as living_plan_mod


def _args(*, status: str = "fixed", note: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(status=status, note=note)


def test_capture_cluster_context_returns_remaining_counts() -> None:
    plan = {
        "overrides": {
            "a": {"cluster": "epic/x"},
            "b": {"cluster": "epic/x"},
        },
        "clusters": {"epic/x": {"issue_ids": ["a", "b", "c"]}},
    }
    ctx = living_plan_mod.capture_cluster_context(plan, ["a", "b"])
    assert ctx.cluster_name == "epic/x"
    assert ctx.cluster_completed is False
    assert ctx.cluster_remaining == 1

    done_ctx = living_plan_mod.capture_cluster_context(plan, ["a", "b", "c"])
    assert done_ctx.cluster_completed is True
    assert done_ctx.cluster_remaining == 0


def test_update_living_plan_after_resolve_no_living_plan(monkeypatch) -> None:
    monkeypatch.setattr(living_plan_mod, "has_living_plan", lambda _p=None: False)
    plan, ctx = living_plan_mod.update_living_plan_after_resolve(
        args=_args(),
        all_resolved=["x"],
        attestation="attest",
    )
    assert plan is None
    assert ctx.cluster_name is None


def test_update_living_plan_after_resolve_fixed_flow(monkeypatch, capsys) -> None:
    plan = {
        "queue_order": ["a"],
        "overrides": {"a": {"cluster": "epic/a"}},
        "clusters": {"epic/a": {"issue_ids": ["a"]}},
    }
    calls: list[str] = []
    monkeypatch.setattr(living_plan_mod, "has_living_plan", lambda _p=None: True)
    monkeypatch.setattr(living_plan_mod, "load_plan", lambda _p=None: plan)
    monkeypatch.setattr(living_plan_mod, "purge_ids", lambda _plan, _ids: 1)
    monkeypatch.setattr(living_plan_mod, "auto_complete_steps", lambda _plan: ["step complete"])
    monkeypatch.setattr(living_plan_mod, "append_log_entry", lambda *_a, **_k: calls.append("log"))
    monkeypatch.setattr(living_plan_mod, "add_uncommitted_issues", lambda *_a, **_k: calls.append("add"))
    monkeypatch.setattr(living_plan_mod, "clear_postflight_scan_completion", lambda *_a, **_k: calls.append("clear"))
    monkeypatch.setattr(living_plan_mod, "save_plan", lambda _plan, _p=None: calls.append("save"))

    updated_plan, ctx = living_plan_mod.update_living_plan_after_resolve(
        args=_args(status="fixed", note="done"),
        all_resolved=["a"],
        attestation="attest",
    )

    out = capsys.readouterr().out
    assert updated_plan is plan
    assert ctx.cluster_completed is True
    assert "step complete" in out
    assert "Plan updated: 1 item(s)" in out
    assert calls.count("log") == 2  # resolve + cluster_done
    assert "add" in calls and "clear" in calls and "save" in calls


def test_update_living_plan_after_resolve_handles_plan_exceptions(monkeypatch, capsys) -> None:
    monkeypatch.setattr(living_plan_mod, "has_living_plan", lambda _p=None: True)
    monkeypatch.setattr(
        living_plan_mod,
        "load_plan",
        lambda _p=None: (_ for _ in ()).throw(OSError("boom")),
    )
    monkeypatch.setattr(living_plan_mod, "PLAN_LOAD_EXCEPTIONS", (OSError,))

    plan, ctx = living_plan_mod.update_living_plan_after_resolve(
        args=_args(),
        all_resolved=["a"],
        attestation="attest",
    )

    err = capsys.readouterr().err
    assert plan is None
    assert ctx.cluster_name is None
    assert "could not update living plan" in err
