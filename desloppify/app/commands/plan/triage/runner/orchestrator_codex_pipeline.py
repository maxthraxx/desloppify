"""Codex pipeline orchestration for triage stages."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import desloppify
from desloppify.app.commands.runner.run_logs import make_run_log_writer
from desloppify.base.discovery.file_paths import safe_write_text
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.exception_sets import CommandError
from desloppify.base.output.terminal import colorize

from ..helpers import has_triage_in_queue, inject_triage_stages
from ..lifecycle import TriageLifecycleDeps, ensure_triage_started
from ..services import TriageServices, default_triage_services
from ..validation.core import (
    _analyze_reflect_issue_accounting,
    _validate_reflect_issue_accounting,
)
from .codex_runner import run_triage_stage
from .orchestrator_codex_pipeline_completion import (
    all_stage_results_successful as all_stage_results_successful_impl,
    build_completion_strategy as build_completion_strategy_impl,
    complete_pipeline as complete_pipeline_impl,
    is_full_stage_run as is_full_stage_run_impl,
    print_not_finalized_message as print_not_finalized_message_impl,
    validate_and_confirm_stage as validate_and_confirm_stage_impl,
)
from .orchestrator_codex_pipeline_context import (
    PipelineRunContext,
    StageRunContext,
    load_prior_reports_from_plan as load_prior_reports_from_plan_ctx,
)
from .orchestrator_codex_pipeline_execution import (
    DEFAULT_STAGE_HANDLERS,
    StageExecutionDependencies,
    StageHandler,
    execute_stage as execute_stage_impl,
    read_stage_output as read_stage_output_impl,
)
from .orchestrator_common import STAGES, run_stamp
from .stage_prompts import build_stage_prompt


def _is_full_stage_run(stages_to_run: list[str]) -> bool:
    """True when the pipeline was asked to run the full triage stage set."""
    return is_full_stage_run_impl(stages_to_run)


def _all_stage_results_successful(
    *,
    stages_to_run: list[str],
    stage_results: dict[str, dict],
) -> bool:
    """True when each requested stage is confirmed or already confirmed."""
    return all_stage_results_successful_impl(
        stages_to_run=stages_to_run,
        stage_results=stage_results,
    )


def _print_not_finalized_message(reason: str) -> None:
    """Emit a consistent next-step message when auto-completion is skipped/blocked."""
    print_not_finalized_message_impl(reason)


def _load_prior_reports_from_plan(plan: dict) -> dict[str, str]:
    """Seed prior stage reports from the existing live triage state."""
    return load_prior_reports_from_plan_ctx(plan, STAGES)


_STAGE_HANDLERS: dict[str, StageHandler] = DEFAULT_STAGE_HANDLERS


@dataclass(frozen=True)
class StageSequenceResult:
    """Outcome of stage execution before pipeline finalization."""

    stage_results: dict[str, dict]
    prior_reports: dict[str, str]
    last_triage_input: dict | None


def _write_desloppify_cli_helper(run_dir: Path) -> Path:
    """Create an exact CLI wrapper so codex subagents use this checkout + interpreter."""
    package_root = Path(desloppify.__file__).resolve().parent.parent
    script_path = run_dir / "run_desloppify.sh"
    script = (
        "#!/bin/sh\n"
        f"export PYTHONPATH={shlex.quote(str(package_root))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        f"exec {shlex.quote(sys.executable)} -m desloppify.cli \"$@\"\n"
    )
    safe_write_text(script_path, script)
    os.chmod(script_path, 0o700)
    return script_path


def _read_stage_output(output_file: Path) -> str:
    """Return stripped stage output text, or an empty string when unreadable."""
    return read_stage_output_impl(output_file)


def _stage_execution_dependencies() -> StageExecutionDependencies:
    """Resolve stage execution dependencies from module symbols for patchability."""
    return StageExecutionDependencies(
        build_stage_prompt=build_stage_prompt,
        run_triage_stage=run_triage_stage,
        read_stage_output=_read_stage_output,
        analyze_reflect_issue_accounting=_analyze_reflect_issue_accounting,
        validate_reflect_issue_accounting=_validate_reflect_issue_accounting,
    )

def _validate_and_confirm_stage(
    *,
    stage: str,
    args: argparse.Namespace,
    services: TriageServices,
    si: dict,
    state,
    repo_root: Path,
    stage_start: float,
    append_run_log,
) -> tuple[bool, dict, str]:
    """Run shared stage validation + confirmation flow."""
    return validate_and_confirm_stage_impl(
        stage=stage,
        args=args,
        services=services,
        triage_input=si,
        state=state,
        repo_root=repo_root,
        stage_start=stage_start,
        append_run_log=append_run_log,
    )


def _fail_stage_and_write_summary(
    *,
    pipeline_context: PipelineRunContext,
    stage_results: dict[str, dict],
    message: str,
) -> None:
    write_triage_run_summary(
        pipeline_context.run_dir,
        pipeline_context.stamp,
        pipeline_context.stages_to_run,
        stage_results,
        pipeline_context.append_run_log,
    )
    raise CommandError(
        f"{message}. See {pipeline_context.run_dir / 'run_summary.json'}",
        exit_code=1,
    )


def _run_stage_sequence(
    *,
    pipeline_context: PipelineRunContext,
    initial_plan: dict,
) -> StageSequenceResult:
    prior_reports = _load_prior_reports_from_plan(initial_plan)
    stage_results: dict[str, dict] = {}
    last_triage_input: dict | None = None

    for stage in pipeline_context.stages_to_run:
        plan = pipeline_context.services.load_plan()
        meta = plan.get("epic_triage_meta", {})
        triage_stages = meta.get("triage_stages", {})

        if stage in triage_stages and triage_stages[stage].get("confirmed_at"):
            print(colorize(f"  Stage {stage}: already confirmed, skipping.", "green"))
            pipeline_context.append_run_log(f"stage-skip stage={stage} reason=already_confirmed")
            stage_results[stage] = {"status": "skipped"}
            report = triage_stages[stage].get("report", "")
            if report:
                prior_reports[stage] = report
            continue

        stage_start = time.monotonic()
        pipeline_context.append_run_log(f"stage-start stage={stage}")

        si = pipeline_context.services.collect_triage_input(plan, pipeline_context.state)
        last_triage_input = si
        exec_status, exec_result = execute_stage_impl(
            StageRunContext(
                stage=stage,
                stage_start=stage_start,
                args=pipeline_context.args,
                services=pipeline_context.services,
                plan=plan,
                triage_input=si,
                prior_reports=prior_reports,
                repo_root=pipeline_context.repo_root,
                prompts_dir=pipeline_context.prompts_dir,
                output_dir=pipeline_context.output_dir,
                logs_dir=pipeline_context.logs_dir,
                cli_command=pipeline_context.cli_command,
                timeout_seconds=pipeline_context.timeout_seconds,
                dry_run=pipeline_context.dry_run,
                append_run_log=pipeline_context.append_run_log,
            ),
            handlers=_STAGE_HANDLERS,
            dependencies=_stage_execution_dependencies(),
        )
        if exec_status == "dry_run":
            stage_results[stage] = exec_result
            continue
        if exec_status == "failed":
            stage_results[stage] = exec_result
            _fail_stage_and_write_summary(
                pipeline_context=pipeline_context,
                stage_results=stage_results,
                message=f"triage stage failed: {stage}",
            )

        confirmed, confirm_result, report = _validate_and_confirm_stage(
            stage=stage,
            args=pipeline_context.args,
            services=pipeline_context.services,
            si=si,
            state=pipeline_context.state,
            repo_root=pipeline_context.repo_root,
            stage_start=stage_start,
            append_run_log=pipeline_context.append_run_log,
        )
        stage_results[stage] = confirm_result
        if not confirmed:
            _fail_stage_and_write_summary(
                pipeline_context=pipeline_context,
                stage_results=stage_results,
                message=f"triage stage validation failed: {stage}",
            )
        if report:
            prior_reports[stage] = report

    return StageSequenceResult(
        stage_results=stage_results,
        prior_reports=prior_reports,
        last_triage_input=last_triage_input,
    )


def _finalize_pipeline_run(
    *,
    pipeline_context: PipelineRunContext,
    stage_results: dict[str, dict],
    pipeline_start: float,
    last_triage_input: dict | None,
) -> None:
    if pipeline_context.dry_run:
        print(colorize("\n  [dry-run] All prompts generated. No stages executed.", "cyan"))
        write_triage_run_summary(
            pipeline_context.run_dir,
            pipeline_context.stamp,
            pipeline_context.stages_to_run,
            stage_results,
            pipeline_context.append_run_log,
        )
        return

    plan = pipeline_context.services.load_plan()
    meta = plan.get("epic_triage_meta", {})
    stages_data = meta.get("triage_stages", {})
    strategy = build_completion_strategy_impl(stages_data)

    should_auto_complete = (
        _is_full_stage_run(pipeline_context.stages_to_run)
        and _all_stage_results_successful(
            stages_to_run=pipeline_context.stages_to_run,
            stage_results=stage_results,
        )
    )
    total_elapsed = int(time.monotonic() - pipeline_start)
    if not should_auto_complete:
        _print_not_finalized_message("partial stage run")
        pipeline_context.append_run_log(
            f"run-finished elapsed={total_elapsed}s finalized=false reason=partial_stage_run"
        )
        write_triage_run_summary(
            pipeline_context.run_dir,
            pipeline_context.stamp,
            pipeline_context.stages_to_run,
            stage_results,
            pipeline_context.append_run_log,
            finalized=False,
            finalization_reason="partial_stage_run",
        )
        return

    triage_input = last_triage_input or pipeline_context.services.collect_triage_input(
        plan,
        pipeline_context.state,
    )
    completed = complete_pipeline_impl(
        args=pipeline_context.args,
        services=pipeline_context.services,
        plan=plan,
        strategy=strategy,
        triage_input=triage_input,
    )
    if not completed:
        _print_not_finalized_message("completion command blocked")
        pipeline_context.append_run_log(
            f"run-finished elapsed={total_elapsed}s finalized=false reason=completion_blocked"
        )
        write_triage_run_summary(
            pipeline_context.run_dir,
            pipeline_context.stamp,
            pipeline_context.stages_to_run,
            stage_results,
            pipeline_context.append_run_log,
            finalized=False,
            finalization_reason="completion_blocked",
        )
        return

    print(colorize(f"\n  Triage pipeline complete ({total_elapsed}s).", "green"))
    pipeline_context.append_run_log(f"run-finished elapsed={total_elapsed}s finalized=true")
    write_triage_run_summary(
        pipeline_context.run_dir,
        pipeline_context.stamp,
        pipeline_context.stages_to_run,
        stage_results,
        pipeline_context.append_run_log,
        finalized=True,
    )


def run_codex_pipeline(
    args: argparse.Namespace,
    *,
    stages_to_run: list[str],
    services: TriageServices | None = None,
) -> None:
    """Run triage stages via Codex subprocesses (automated pipeline)."""
    resolved_services = services or default_triage_services()
    timeout_seconds = int(getattr(args, "stage_timeout_seconds", 1800) or 1800)
    dry_run = bool(getattr(args, "dry_run", False))

    repo_root = get_project_root()
    runtime = resolved_services.command_runtime(args)
    state = runtime.state
    plan = resolved_services.load_plan()
    start_outcome = ensure_triage_started(
        plan,
        services=resolved_services,
        state=state,
        attestation=getattr(args, "attestation", None),
        log_action="triage_auto_start",
        log_actor="system",
        log_detail={
            "source": "runner_auto_start",
            "runner": "codex",
            "injected_stage_ids": list(STAGES),
        },
        start_message="  Planning mode auto-started.",
        deps=TriageLifecycleDeps(
            has_triage_in_queue=has_triage_in_queue,
            inject_triage_stages=inject_triage_stages,
        ),
    )
    if getattr(start_outcome, "status", None) == "blocked":
        return
    plan = resolved_services.load_plan()

    stamp = run_stamp()
    desloppify_dir = repo_root / ".desloppify"
    run_dir = desloppify_dir / "triage_runs" / stamp
    prompts_dir = run_dir / "prompts"
    output_dir = run_dir / "output"
    logs_dir = run_dir / "logs"
    for output_path in (prompts_dir, output_dir, logs_dir):
        output_path.mkdir(parents=True, exist_ok=True)

    run_log_path = run_dir / "run.log"
    append_run_log = make_run_log_writer(run_log_path)
    cli_helper = _write_desloppify_cli_helper(run_dir)
    append_run_log(
        f"run-start runner=codex stages={','.join(stages_to_run)} "
        f"timeout={timeout_seconds}s dry_run={dry_run}"
    )

    pipeline_context = PipelineRunContext(
        args=args,
        services=resolved_services,
        state=state,
        stages_to_run=stages_to_run,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        repo_root=repo_root,
        stamp=stamp,
        run_dir=run_dir,
        prompts_dir=prompts_dir,
        output_dir=output_dir,
        logs_dir=logs_dir,
        run_log_path=run_log_path,
        cli_command=str(cli_helper),
        append_run_log=append_run_log,
    )

    print(colorize(f"  Run artifacts: {pipeline_context.run_dir}", "dim"))
    print(colorize(f"  Live run log:  {pipeline_context.run_log_path}", "dim"))
    print(colorize(f"  CLI helper:    {pipeline_context.cli_command}", "dim"))

    pipeline_start = time.monotonic()
    stage_sequence = _run_stage_sequence(
        pipeline_context=pipeline_context,
        initial_plan=plan,
    )
    _finalize_pipeline_run(
        pipeline_context=pipeline_context,
        stage_results=stage_sequence.stage_results,
        pipeline_start=pipeline_start,
        last_triage_input=stage_sequence.last_triage_input,
    )


def write_triage_run_summary(
    run_dir: Path,
    stamp: str,
    stages: list[str],
    stage_results: dict[str, dict],
    append_run_log,
    *,
    finalized: bool | None = None,
    finalization_reason: str | None = None,
) -> None:
    """Write a run_summary.json with per-stage results."""
    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_stamp": stamp,
        "runner": "codex",
        "stages_requested": stages,
        "stage_results": stage_results,
        "run_dir": str(run_dir),
    }
    if finalized is not None:
        summary["finalized"] = finalized
    if finalization_reason:
        summary["finalization_reason"] = finalization_reason
    summary_path = run_dir / "run_summary.json"
    safe_write_text(summary_path, json.dumps(summary, indent=2) + "\n")
    print(colorize(f"  Run summary: {summary_path}", "dim"))
    append_run_log(f"run-summary {summary_path}")


__all__ = ["run_codex_pipeline"]
