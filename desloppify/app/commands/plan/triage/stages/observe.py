"""Observe stage command flow."""

from __future__ import annotations

import argparse

from desloppify.base.output.terminal import colorize
from desloppify.base.output.user_message import print_user_message

from ..helpers import has_triage_in_queue, inject_triage_stages, print_cascade_clear_feedback
from ..lifecycle import TriageLifecycleDeps, ensure_triage_started
from ..services import TriageServices, default_triage_services
from .flow_helpers import validate_stage_report_length
from .records import record_observe_stage, resolve_reusable_report
from .rendering import _print_observe_report_requirement


def _cmd_stage_observe(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
    has_triage_in_queue_fn=has_triage_in_queue,
    inject_triage_stages_fn=inject_triage_stages,
) -> None:
    """Record the OBSERVE stage: agent analyses themes and root causes."""
    report: str | None = getattr(args, "report", None)
    attestation: str | None = getattr(args, "attestation", None)

    resolved_services = services or default_triage_services()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    plan = resolved_services.load_plan()

    if not has_triage_in_queue_fn(plan):
        start_outcome = ensure_triage_started(
            plan,
            services=resolved_services,
            state=state,
            attestation=attestation,
            start_message="  Planning mode auto-started (6 stages queued).",
            deps=TriageLifecycleDeps(
                has_triage_in_queue=has_triage_in_queue_fn,
                inject_triage_stages=inject_triage_stages_fn,
            ),
        )
        if start_outcome.status == "blocked":
            return

    meta = plan.setdefault("epic_triage_meta", {})
    stages = meta.setdefault("triage_stages", {})
    existing_stage = stages.get("observe")

    report, is_reuse = resolve_reusable_report(report, existing_stage)
    if not report:
        _print_observe_report_requirement()
        return

    si = resolved_services.collect_triage_input(plan, state)
    issue_count = len(si.open_issues)
    if issue_count == 0:
        cleared = record_observe_stage(
            stages,
            report=report,
            issue_count=0,
            cited_ids=[],
            existing_stage=existing_stage,
            is_reuse=is_reuse,
        )
        resolved_services.save_plan(plan)
        print(colorize("  Observe stage recorded (no issues to analyse).", "green"))
        if is_reuse:
            print(colorize("  Observe data preserved (no changes).", "dim"))
            if cleared:
                print_cascade_clear_feedback(cleared, stages)
        return

    if not validate_stage_report_length(
        report=report,
        issue_count=issue_count,
        guidance="  Describe themes, root causes, contradictions, and how issues relate.",
    ):
        return

    from .evidence_parsing import (
        format_evidence_failures,
        parse_observe_evidence,
        validate_observe_evidence,
    )

    valid_ids = set(si.open_issues.keys())
    cited = resolved_services.extract_issue_citations(report, valid_ids)
    evidence = parse_observe_evidence(report, valid_ids)
    evidence_failures = validate_observe_evidence(evidence, issue_count)
    blocking = [failure for failure in evidence_failures if failure.blocking]
    advisory = [failure for failure in evidence_failures if not failure.blocking]
    if blocking:
        print(colorize(format_evidence_failures(blocking, stage_label="observe"), "red"))
        return
    if advisory:
        print(colorize(format_evidence_failures(advisory, stage_label="observe"), "yellow"))

    assessments = [
        {
            "hash": entry.issue_hash,
            "verdict": entry.verdict,
            "verdict_reasoning": entry.verdict_reasoning,
            "files_read": entry.files_read,
            "recommendation": entry.recommendation,
        }
        for entry in evidence.entries
    ]
    cleared = record_observe_stage(
        stages,
        report=report,
        issue_count=issue_count,
        cited_ids=sorted(cited),
        existing_stage=existing_stage,
        is_reuse=is_reuse,
        assessments=assessments,
    )
    resolved_services.save_plan(plan)
    resolved_services.append_log_entry(
        plan,
        "triage_observe",
        actor="user",
        detail={"issue_count": issue_count, "cited_ids": sorted(cited), "reuse": is_reuse},
    )
    resolved_services.save_plan(plan)

    print(colorize(f"  Observe stage recorded: {issue_count} issues analysed.", "green"))
    if is_reuse:
        print(colorize("  Observe data preserved (no changes).", "dim"))
        if cleared:
            print_cascade_clear_feedback(cleared, stages)
        return

    print(colorize("  Now confirm your analysis.", "yellow"))
    print(colorize("    desloppify plan triage --confirm observe", "dim"))
    print_user_message(
        "Observe recorded. Before confirming — did the subagent"
        " verify every issue with code reads? Check: are there"
        " specific file/line citations in the report, or just"
        " restated issue titles? Each issue needs a verdict:"
        " genuine / false positive / exaggerated. Don't confirm"
        " until the analysis is backed by actual code evidence."
    )


def cmd_stage_observe(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for observe stage recording."""
    _cmd_stage_observe(args, services=services)


__all__ = ["_cmd_stage_observe", "cmd_stage_observe"]
