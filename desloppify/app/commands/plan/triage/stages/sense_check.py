"""Sense-check stage command flow."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.output.terminal import colorize

from .records import record_sense_check_stage, resolve_reusable_report
from ..validation.enrich_quality import evaluate_enrich_quality
from ..validation.enrich_checks import (
    _steps_missing_issue_refs,
    _steps_with_bad_paths,
    _steps_with_vague_detail,
    _steps_without_effort,
    _underspecified_steps,
)
from ..helpers import has_triage_in_queue, open_review_ids_from_state, print_cascade_clear_feedback
from ..services import TriageServices, default_triage_services
from .enrich import ColorizeFn


@dataclass(frozen=True)
class SenseCheckStageDeps:
    has_triage_in_queue: Callable[[dict], bool] = has_triage_in_queue
    resolve_reusable_report: Callable[[str | None, dict | None], tuple[str | None, bool]] = (
        resolve_reusable_report
    )
    record_sense_check_stage: Callable[..., list[str]] = record_sense_check_stage
    colorize: ColorizeFn = colorize
    default_triage_services: Callable[[], TriageServices] = default_triage_services
    print_cascade_clear_feedback: Callable[[list[str], dict], None] = print_cascade_clear_feedback
    get_project_root: Callable[[], Path] | None = None
    underspecified_steps: Callable[[dict], list[tuple[str, int, int]]] = _underspecified_steps
    steps_missing_issue_refs: Callable[[dict], list[tuple[str, int, int]]] = _steps_missing_issue_refs
    steps_with_bad_paths: Callable[[dict, Path], list[tuple[str, int, list[str]]]] = _steps_with_bad_paths
    steps_with_vague_detail: Callable[[dict, Path], list[tuple[str, int, str]]] = _steps_with_vague_detail
    steps_without_effort: Callable[[dict], list[tuple[str, int, int]]] = _steps_without_effort


def run_stage_sense_check(
    args: argparse.Namespace,
    *,
    services: TriageServices | None,
    deps: SenseCheckStageDeps | None = None,
) -> None:
    """Record the SENSE-CHECK stage after rerunning enrich-level validations."""
    resolved_deps = deps or SenseCheckStageDeps()
    report: str | None = getattr(args, "report", None)

    resolved_services = services or resolved_deps.default_triage_services()
    plan = resolved_services.load_plan()
    state = resolved_services.command_runtime(args).state

    if not resolved_deps.has_triage_in_queue(plan):
        print(resolved_deps.colorize("  No planning stages in the queue — nothing to sense-check.", "yellow"))
        return

    meta = plan.get("epic_triage_meta", {})
    stages = meta.get("triage_stages", {})

    existing_stage = stages.get("sense-check")
    report, is_reuse = resolved_deps.resolve_reusable_report(report, existing_stage)

    if not stages.get("enrich", {}).get("confirmed_at"):
        print(resolved_deps.colorize("  Cannot sense-check: enrich stage not confirmed.", "red"))
        print(resolved_deps.colorize("  Run: desloppify plan triage --confirm enrich", "dim"))
        return

    get_project_root = resolved_deps.get_project_root
    if get_project_root is None:
        from desloppify.base.discovery.paths import get_project_root

    repo_root = get_project_root()
    quality_report = evaluate_enrich_quality(
        plan,
        repo_root,
        phase_label="sense-check",
        bad_paths_severity="failure",
        missing_effort_severity="failure",
        include_missing_issue_refs=True,
        include_vague_detail=True,
        stale_issue_refs_severity=None,
    )
    problems = [issue.message for issue in quality_report.failures]

    if problems:
        print(resolved_deps.colorize("  Cannot record sense-check — plan still has issues:", "red"))
        for problem in problems:
            print(resolved_deps.colorize(f"    • {problem}", "yellow"))
        print(resolved_deps.colorize("  Fix these before recording the sense-check stage.", "dim"))
        return

    print(resolved_deps.colorize("  All enrich-level checks pass after sense-check.", "green"))

    if not report:
        print(resolved_deps.colorize("  --report is required for --stage sense-check.", "red"))
        print(
            resolved_deps.colorize(
                "  Describe what the content and structure subagents found and fixed.",
                "dim",
            )
        )
        return

    if len(report) < 100:
        print(resolved_deps.colorize(f"  Report too short: {len(report)} chars (minimum 100).", "red"))
        return

    from .evidence_parsing import (
        EvidenceFailure,
        format_evidence_failures,
        validate_report_has_file_paths,
        validate_report_references_clusters,
    )
    from ..helpers import manual_clusters_with_issues

    sc_evidence_failures: list[EvidenceFailure] = []
    has_open_review_work = bool(open_review_ids_from_state(state))
    if has_open_review_work:
        path_failures = validate_report_has_file_paths(report)
        if path_failures:
            sc_evidence_failures.extend(path_failures)

    cluster_names = manual_clusters_with_issues(plan)
    if cluster_names:
        cluster_failures = validate_report_references_clusters(report, cluster_names)
        if cluster_failures:
            sc_evidence_failures.extend(cluster_failures)

    blocking_ev = [f for f in sc_evidence_failures if f.blocking]
    advisory_ev = [f for f in sc_evidence_failures if not f.blocking]
    if blocking_ev:
        msg = format_evidence_failures(blocking_ev, stage_label="sense-check")
        print(resolved_deps.colorize(msg, "red"))
        return
    if advisory_ev:
        msg = format_evidence_failures(advisory_ev, stage_label="sense-check")
        print(resolved_deps.colorize(msg, "yellow"))

    stages = meta.setdefault("triage_stages", {})
    cleared = resolved_deps.record_sense_check_stage(
        stages,
        report=report,
        existing_stage=existing_stage,
        is_reuse=is_reuse,
    )

    resolved_services.save_plan(plan)

    resolved_services.append_log_entry(
        plan,
        "triage_sense_check",
        actor="user",
        detail={"reuse": is_reuse},
    )
    resolved_services.save_plan(plan)

    print(resolved_deps.colorize("  Sense-check stage recorded.", "green"))
    if is_reuse:
        print(resolved_deps.colorize("  Sense-check data preserved (no changes).", "dim"))
        if cleared:
            resolved_deps.print_cascade_clear_feedback(cleared, stages)
    else:
        print(resolved_deps.colorize("  Now confirm the sense-check.", "yellow"))
        print(resolved_deps.colorize("    desloppify plan triage --confirm sense-check", "dim"))


def cmd_stage_sense_check(
    args: argparse.Namespace,
    *,
    services: TriageServices | None = None,
) -> None:
    """Public entrypoint for sense-check stage recording."""
    run_stage_sense_check(args, services=services)


__all__ = [
    "SenseCheckStageDeps",
    "cmd_stage_sense_check",
    "record_sense_check_stage",
    "run_stage_sense_check",
]
