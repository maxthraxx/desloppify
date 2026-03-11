"""Reflect stage command flow."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.state_io import utc_now

from ..display.dashboard import print_reflect_result
from ..helpers import cascade_clear_later_confirmations, has_triage_in_queue
from ..services import TriageServices, default_triage_services
from ..validation.core import (
    _auto_confirm_observe_if_attested,
    _validate_recurring_dimension_mentions,
    _validate_reflect_issue_accounting,
)
from .flow_helpers import validate_stage_report_length
from .records import resolve_reusable_report
from .rendering import _print_reflect_report_requirement


def _validate_reflect_submission(
    *,
    report: str,
    plan: dict,
    state: dict,
    stages: dict,
    attestation: str | None,
    services: TriageServices,
) -> tuple[object, int, dict, list[str], set[str], list[str], list[str]] | None:
    if "observe" not in stages:
        print(colorize("  Cannot reflect: observe stage not complete.", "red"))
        print(colorize('  Run: desloppify plan triage --stage observe --report "..."', "dim"))
        return None

    triage_input = services.collect_triage_input(plan, state)
    if not _auto_confirm_observe_if_attested(
        plan=plan,
        stages=stages,
        attestation=attestation,
        triage_input=triage_input,
        save_plan_fn=services.save_plan,
    ):
        return None

    issue_count = len(triage_input.open_issues)
    if not validate_stage_report_length(
        report=report,
        issue_count=issue_count,
        guidance="  Describe how current issues relate to previously completed work.",
    ):
        return None

    recurring = services.detect_recurring_patterns(
        triage_input.open_issues,
        triage_input.resolved_issues,
    )
    recurring_dims = sorted(recurring.keys())
    if not _validate_recurring_dimension_mentions(
        report=report,
        recurring_dims=recurring_dims,
        recurring=recurring,
    ):
        return None

    accounting_ok, cited_ids, missing_ids, duplicate_ids = _validate_reflect_issue_accounting(
        report=report,
        valid_ids=set(triage_input.open_issues.keys()),
    )
    if not accounting_ok:
        return None

    from .evidence_parsing import format_evidence_failures, validate_reflect_skip_evidence

    blocking_skips = [
        failure
        for failure in validate_reflect_skip_evidence(report)
        if failure.blocking
    ]
    if blocking_skips:
        print(colorize(format_evidence_failures(blocking_skips, stage_label="reflect"), "red"))
        return None

    return (
        triage_input,
        issue_count,
        recurring,
        recurring_dims,
        cited_ids,
        missing_ids,
        duplicate_ids,
    )


def _persist_reflect_stage(
    *,
    plan: dict,
    meta: dict,
    stages: dict,
    report: str,
    issue_count: int,
    cited_ids: set[str],
    missing_ids: list[str],
    duplicate_ids: list[str],
    recurring_dims: list[str],
    existing_stage: dict | None,
    is_reuse: bool,
    services: TriageServices,
) -> tuple[dict, list[str]]:
    stages = meta.setdefault("triage_stages", {})
    reflect_stage = {
        "stage": "reflect",
        "report": report,
        "cited_ids": sorted(cited_ids),
        "timestamp": utc_now(),
        "issue_count": issue_count,
        "missing_issue_ids": missing_ids,
        "duplicate_issue_ids": duplicate_ids,
        "recurring_dims": recurring_dims,
    }
    stages["reflect"] = reflect_stage
    if is_reuse and existing_stage and existing_stage.get("confirmed_at"):
        reflect_stage["confirmed_at"] = existing_stage["confirmed_at"]
        reflect_stage["confirmed_text"] = existing_stage.get("confirmed_text", "")
    cleared = cascade_clear_later_confirmations(stages, "reflect")

    services.save_plan(plan)
    services.append_log_entry(
        plan,
        "triage_reflect",
        actor="user",
        detail={"issue_count": issue_count, "reuse": is_reuse, "recurring_dims": recurring_dims},
    )
    services.save_plan(plan)
    return reflect_stage, cleared


def _cmd_stage_reflect(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Record the REFLECT stage: compare current issues against completed work."""
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    resolved_services = services or default_triage_services()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    plan = resolved_services.load_plan()

    if not has_triage_in_queue(plan):
        print(colorize("  No planning stages in the queue — nothing to reflect on.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})
    existing_stage = stages.get("reflect")

    report, is_reuse = resolve_reusable_report(report, existing_stage)
    if not report:
        _print_reflect_report_requirement()
        return

    submission = _validate_reflect_submission(
        report=report,
        plan=plan,
        state=state,
        stages=stages,
        attestation=attestation,
        services=resolved_services,
    )
    if submission is None:
        return
    triage_input, issue_count, recurring, recurring_dims, cited_ids, missing_ids, duplicate_ids = submission
    reflect_stage, cleared = _persist_reflect_stage(
        plan=plan,
        meta=meta,
        stages=stages,
        report=report,
        issue_count=issue_count,
        cited_ids=cited_ids,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        recurring_dims=recurring_dims,
        existing_stage=existing_stage,
        is_reuse=is_reuse,
        services=resolved_services,
    )

    print_reflect_result(
        issue_count=issue_count,
        recurring_dims=recurring_dims,
        recurring=recurring,
        report=report,
        is_reuse=is_reuse,
        cleared=cleared,
        stages=stages,
    )


def cmd_stage_reflect(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for reflect stage recording."""
    _cmd_stage_reflect(args, services=services)


__all__ = ["_cmd_stage_reflect", "cmd_stage_reflect"]
