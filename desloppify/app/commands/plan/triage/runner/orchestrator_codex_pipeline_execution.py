"""Stage execution collaborators for the Codex triage pipeline."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import safe_write_text
from desloppify.base.output.terminal import colorize

from ..services import TriageServices
from ..validation.core import (
    _analyze_reflect_issue_accounting,
    _validate_reflect_issue_accounting,
)
from .codex_runner import TriageStageRunResult, run_triage_stage
from .orchestrator_codex_observe import run_observe
from .orchestrator_codex_pipeline_context import StageRunContext
from .orchestrator_codex_sense import run_sense_check
from .stage_prompts import build_stage_prompt
from ..validation.core import _missing_stage_prerequisite
from .stage_prompts_instruction_shared import PromptMode


def read_stage_output(output_file: Path) -> str:
    """Return stripped stage output text, or an empty string when unreadable."""
    try:
        return output_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class StageHandler:
    """Per-stage execution/record hooks for the codex triage pipeline."""

    run_parallel: Callable[[StageRunContext], TriageStageRunResult] | None = None
    record_report: Callable[[str, argparse.Namespace, TriageServices], None] | None = None
    prompt_mode: PromptMode = "output_only"


def _record_observe_report(
    report: str,
    args: argparse.Namespace,
    services: TriageServices,
) -> None:
    from ..stages.commands import cmd_stage_observe

    record_args = argparse.Namespace(
        stage="observe",
        report=report,
        state=getattr(args, "state", None),
    )
    cmd_stage_observe(record_args, services=services)


def _record_reflect_report(
    report: str,
    args: argparse.Namespace,
    services: TriageServices,
) -> None:
    from ..stages.commands import cmd_stage_reflect

    record_args = argparse.Namespace(
        stage="reflect",
        report=report,
        state=getattr(args, "state", None),
    )
    cmd_stage_reflect(record_args, services=services)


def _record_sense_check_report(
    report: str,
    args: argparse.Namespace,
    services: TriageServices,
) -> None:
    from ..stages.commands import cmd_stage_sense_check

    record_args = argparse.Namespace(
        stage="sense-check",
        report=report,
        state=getattr(args, "state", None),
    )
    cmd_stage_sense_check(record_args, services=services)


DEFAULT_STAGE_HANDLERS: dict[str, StageHandler] = {
    "observe": StageHandler(
        run_parallel=lambda context: run_observe(
            si=context.triage_input,
            repo_root=context.repo_root,
            prompts_dir=context.prompts_dir,
            output_dir=context.output_dir,
            logs_dir=context.logs_dir,
            timeout_seconds=context.timeout_seconds,
            dry_run=context.dry_run,
            append_run_log=context.append_run_log,
        ),
        record_report=_record_observe_report,
    ),
    "reflect": StageHandler(
        record_report=_record_reflect_report,
    ),
    "organize": StageHandler(
        prompt_mode="self_record",
    ),
    "enrich": StageHandler(
        prompt_mode="self_record",
    ),
    "sense-check": StageHandler(
        run_parallel=lambda context: run_sense_check(
            plan=dict(context.plan),
            repo_root=context.repo_root,
            prompts_dir=context.prompts_dir,
            output_dir=context.output_dir,
            logs_dir=context.logs_dir,
            timeout_seconds=context.timeout_seconds,
            dry_run=context.dry_run,
            cli_command=context.cli_command,
            apply_updates=True,
            reload_plan=context.services.load_plan,
            append_run_log=context.append_run_log,
        ),
        record_report=_record_sense_check_report,
    ),
}


@dataclass(frozen=True)
class StageExecutionDependencies:
    """Dependency container for stage execution to support focused patching in tests."""

    build_stage_prompt: Callable[..., str]
    run_triage_stage: Callable[..., TriageStageRunResult]
    read_stage_output: Callable[[Path], str]
    analyze_reflect_issue_accounting: Callable[..., tuple[set[str], list[str], list[str]]]
    validate_reflect_issue_accounting: Callable[
        ...,
        tuple[bool, set[str], list[str], list[str]],
    ]


def default_stage_execution_dependencies() -> StageExecutionDependencies:
    """Construct the default stage execution dependency set."""
    return StageExecutionDependencies(
        build_stage_prompt=build_stage_prompt,
        run_triage_stage=run_triage_stage,
        read_stage_output=read_stage_output,
        analyze_reflect_issue_accounting=_analyze_reflect_issue_accounting,
        validate_reflect_issue_accounting=_validate_reflect_issue_accounting,
    )


def stage_report_recorded(plan: Mapping[str, Any], stage: str) -> bool:
    """True when the plan contains a persisted report for the given stage."""
    return bool(
        plan.get("epic_triage_meta", {})
        .get("triage_stages", {})
        .get(stage, {})
        .get("report", "")
    )


def preflight_stage(
    *,
    stage: str,
    plan: Mapping[str, Any],
    triage_input: Any,
    dry_run: bool,
    append_run_log: Callable[[str], None],
    validate_reflect_issue_accounting: Callable[
        ...,
        tuple[bool, set[str], list[str], list[str]],
    ],
) -> tuple[bool, str | None]:
    """Fail fast when a requested stage has invalid upstream prerequisites."""
    # Dry-run previews do not persist upstream stage reports, so preflight checks that
    # require recorded reflect/enrich state would otherwise fail by construction.
    if dry_run and stage in {"organize", "sense-check"}:
        return True, None

    if stage == "sense-check":
        stages = plan.get("epic_triage_meta", {}).get("triage_stages", {})
        if _missing_stage_prerequisite(stages, flow="sense-check") is None:
            return True, None
        reason = "enrich_not_confirmed"
        append_run_log(f"stage-preflight-failed stage={stage} reason={reason}")
        return False, reason

    if stage != "organize":
        return True, None
    reflect_report = str(
        plan.get("epic_triage_meta", {})
        .get("triage_stages", {})
        .get("reflect", {})
        .get("report", "")
    )
    accounting_ok, _cited, missing_ids, duplicate_ids = validate_reflect_issue_accounting(
        report=reflect_report,
        valid_ids=set(getattr(triage_input, "open_issues", {}).keys()),
    )
    if accounting_ok:
        return True, None
    reason_parts: list[str] = []
    if missing_ids:
        reason_parts.append(f"missing={len(missing_ids)}")
    if duplicate_ids:
        reason_parts.append(f"duplicates={len(duplicate_ids)}")
    reason = "reflect_accounting_invalid"
    if reason_parts:
        reason = f"{reason}({' '.join(reason_parts)})"
    append_run_log(f"stage-preflight-failed stage={stage} reason={reason}")
    return False, reason


def build_reflect_repair_prompt(
    *,
    triage_input: Any,
    prior_reports: Mapping[str, str],
    repo_root: Path,
    cli_command: str,
    original_report: str,
    missing_ids: list[str],
    duplicate_ids: list[str],
    build_stage_prompt_fn: Callable[..., str],
    stages_data: Mapping[str, Any] | None = None,
) -> str:
    """Build a targeted retry prompt for a reflect report that failed accounting."""
    missing_short = ", ".join(issue_id.rsplit("::", 1)[-1] for issue_id in missing_ids) or "none"
    duplicate_short = (
        ", ".join(issue_id.rsplit("::", 1)[-1] for issue_id in duplicate_ids) or "none"
    )
    base_prompt = build_stage_prompt_fn(
        "reflect",
        triage_input,
        dict(prior_reports),
        repo_root=repo_root,
        mode="output_only",
        cli_command=cli_command,
        stages_data=stages_data,
    )
    return "\n\n".join(
        [
            base_prompt,
            "## Repair Pass",
            "Your previous reflect report failed the exact-hash accounting check.",
            f"Missing hashes: {missing_short}",
            f"Duplicated hashes: {duplicate_short}",
            "Rewrite the FULL reflect report so it passes validation.",
            "Requirements for this repair:",
            "- Start with a `## Coverage Ledger` section.",
            '- Use one ledger line per issue hash: `- abcd1234 -> cluster "name"` or `- abcd1234 -> skip "reason"`.',
            "- Mention every required hash exactly once in that ledger.",
            "- Do not mention hashes anywhere else in the report.",
            "- Preserve the same strategy unless fixing the missing/duplicate hashes forces a small adjustment.",
            "- Output only the corrected reflect report.",
            "## Previous Reflect Report",
            original_report,
        ]
    )


def repair_reflect_report_if_needed(
    *,
    report: str,
    triage_input: Any,
    prior_reports: Mapping[str, str],
    repo_root: Path,
    prompts_dir: Path,
    output_dir: Path,
    logs_dir: Path,
    cli_command: str,
    timeout_seconds: int,
    append_run_log: Callable[[str], None],
    dependencies: StageExecutionDependencies,
    stages_data: Mapping[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Retry reflect once with a targeted repair prompt when accounting is invalid."""
    _cited, missing_ids, duplicate_ids = dependencies.analyze_reflect_issue_accounting(
        report=report,
        valid_ids=set(getattr(triage_input, "open_issues", {}).keys()),
    )
    if not missing_ids and not duplicate_ids:
        return report, None

    print(colorize("  Reflect: repairing missing/duplicate hash accounting...", "yellow"))
    append_run_log(
        "stage-reflect-repair-start "
        f"missing={len(missing_ids)} duplicates={len(duplicate_ids)}"
    )

    repair_prompt = build_reflect_repair_prompt(
        triage_input=triage_input,
        prior_reports=prior_reports,
        repo_root=repo_root,
        cli_command=cli_command,
        original_report=report,
        missing_ids=missing_ids,
        duplicate_ids=duplicate_ids,
        build_stage_prompt_fn=dependencies.build_stage_prompt,
        stages_data=stages_data,
    )
    repair_prompt_file = prompts_dir / "reflect.repair.md"
    repair_output_file = output_dir / "reflect.repair.raw.txt"
    repair_log_file = logs_dir / "reflect.repair.log"
    safe_write_text(repair_prompt_file, repair_prompt)
    stage_result = dependencies.run_triage_stage(
        prompt=repair_prompt,
        repo_root=repo_root,
        output_file=repair_output_file,
        log_file=repair_log_file,
        timeout_seconds=timeout_seconds,
    )
    append_run_log(f"stage-reflect-repair-done code={stage_result.exit_code}")
    if not stage_result.ok:
        return None, f"reflect_repair_failed_exit_{stage_result.exit_code}"

    repaired_report = dependencies.read_stage_output(repair_output_file)
    if not repaired_report:
        return None, "reflect_repair_empty_output"

    _cited, missing_after, duplicates_after = dependencies.analyze_reflect_issue_accounting(
        report=repaired_report,
        valid_ids=set(getattr(triage_input, "open_issues", {}).keys()),
    )
    if missing_after or duplicates_after:
        return None, "reflect_repair_invalid"

    print(colorize("  Reflect: repair pass fixed issue accounting.", "green"))
    append_run_log("stage-reflect-repair-success")
    return repaired_report, None


def _execute_parallel_stage(
    *,
    context: StageRunContext,
    stage: str,
    handler: StageHandler | None,
) -> tuple[str, dict, bool]:
    """Execute optional parallel stage path."""
    if handler is None or handler.run_parallel is None:
        return "ready", {}, False

    parallel_result = handler.run_parallel(context)
    if parallel_result.status == "dry_run":
        return "dry_run", {"status": "dry_run"}, True

    if parallel_result.ok and parallel_result.merged_output:
        if handler.record_report is not None:
            handler.record_report(parallel_result.merged_output, context.args, context.services)
            return "ready", {}, True
        return "ready", {}, False

    if parallel_result.ok:
        return "ready", {}, False

    elapsed = int(time.monotonic() - context.stage_start)
    error_reason = parallel_result.reason or "parallel_execution_failed"
    print(colorize(f"  {stage.capitalize()}: parallel execution failed. Aborting.", "red"))
    context.append_run_log(
        f"stage-failed stage={stage} elapsed={elapsed}s reason={error_reason}"
    )
    return "failed", {
        "status": "failed",
        "elapsed_seconds": elapsed,
        "error": error_reason,
    }, True


def _build_subprocess_prompt(
    *,
    context: StageRunContext,
    stage: str,
    prompt_mode: PromptMode,
    dependencies: StageExecutionDependencies,
) -> tuple[str, Mapping[str, Any]]:
    """Build and persist one stage prompt for subprocess execution."""
    stages_data = context.plan.get("epic_triage_meta", {}).get("triage_stages", {})
    prompt = dependencies.build_stage_prompt(
        stage,
        context.triage_input,
        dict(context.prior_reports),
        repo_root=context.repo_root,
        mode=prompt_mode,
        cli_command=context.cli_command,
        stages_data=stages_data,
    )
    prompt_file = context.prompts_dir / f"{stage}.md"
    safe_write_text(prompt_file, prompt)
    return prompt, stages_data


def _run_subprocess_stage(
    *,
    context: StageRunContext,
    stage: str,
    prompt: str,
    dependencies: StageExecutionDependencies,
) -> tuple[str, dict, Path | None, int | None]:
    """Run codex subprocess for one stage (or emit dry-run status)."""
    prompt_file = context.prompts_dir / f"{stage}.md"
    if context.dry_run:
        print(colorize(f"  Stage {stage}: prompt written to {prompt_file}", "cyan"))
        print(colorize("  [dry-run] Would execute codex subprocess.", "dim"))
        return "dry_run", {"status": "dry_run"}, None, None

    print(colorize(f"\n  Stage {stage}: launching codex subprocess...", "bold"))
    context.append_run_log(f"stage-subprocess-start stage={stage}")

    output_file = context.output_dir / f"{stage}.raw.txt"
    log_file = context.logs_dir / f"{stage}.log"
    stage_result = dependencies.run_triage_stage(
        prompt=prompt,
        repo_root=context.repo_root,
        output_file=output_file,
        log_file=log_file,
        timeout_seconds=context.timeout_seconds,
    )

    elapsed = int(time.monotonic() - context.stage_start)
    context.append_run_log(
        "stage-subprocess-done "
        f"stage={stage} code={stage_result.exit_code} elapsed={elapsed}s"
    )

    if stage_result.ok:
        return "ready", {}, output_file, elapsed

    print(
        colorize(
            "  Stage "
            f"{stage}: codex subprocess failed (exit {stage_result.exit_code}).",
            "red",
        )
    )
    print(colorize(f"  Check log: {log_file}", "dim"))
    print(colorize("  Re-run to resume (confirmed stages are skipped).", "dim"))
    context.append_run_log(
        "stage-failed "
        f"stage={stage} elapsed={elapsed}s code={stage_result.exit_code}"
    )
    return "failed", {
        "status": "failed",
        "exit_code": stage_result.exit_code,
        "elapsed_seconds": elapsed,
    }, None, elapsed


def _record_stage_report_if_needed(
    *,
    context: StageRunContext,
    stage: str,
    handler: StageHandler | None,
    dependencies: StageExecutionDependencies,
    output_file: Path,
    elapsed: int,
    stages_data: Mapping[str, Any],
) -> tuple[str, dict]:
    """Record subprocess output for stages that require orchestrator persistence."""
    if handler is None or handler.record_report is None:
        return "ready", {}

    report = dependencies.read_stage_output(output_file)
    if not report:
        print(colorize(f"  Stage {stage}: output file was empty after subprocess.", "red"))
        context.append_run_log(
            f"stage-failed stage={stage} elapsed={elapsed}s reason=empty_stage_output"
        )
        return "failed", {
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": "empty_stage_output",
        }

    if stage == "reflect":
        report, repair_error = repair_reflect_report_if_needed(
            report=report,
            triage_input=context.triage_input,
            prior_reports=context.prior_reports,
            repo_root=context.repo_root,
            prompts_dir=context.prompts_dir,
            output_dir=context.output_dir,
            logs_dir=context.logs_dir,
            cli_command=context.cli_command,
            timeout_seconds=context.timeout_seconds,
            append_run_log=context.append_run_log,
            dependencies=dependencies,
            stages_data=stages_data,
        )
        if repair_error:
            print(
                colorize(
                    f"  Stage {stage}: repair failed ({repair_error}).",
                    "red",
                )
            )
            context.append_run_log(
                f"stage-failed stage={stage} elapsed={elapsed}s reason={repair_error}"
            )
            return "failed", {
                "status": "failed",
                "elapsed_seconds": elapsed,
                "error": repair_error,
            }
        if not report:
            context.append_run_log(
                f"stage-failed stage={stage} elapsed={elapsed}s reason=reflect_repair_no_report"
            )
            return "failed", {
                "status": "failed",
                "elapsed_seconds": elapsed,
                "error": "reflect_repair_no_report",
            }

    handler.record_report(report, context.args, context.services)
    plan_after_record = context.services.load_plan()
    if not stage_report_recorded(plan_after_record, stage):
        print(
            colorize(
                f"  Stage {stage}: handler completed but did not persist the stage.",
                "red",
            )
        )
        context.append_run_log(
            f"stage-record-failed stage={stage} elapsed={elapsed}s reason=stage_not_recorded"
        )
        return "failed", {
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": "stage_not_recorded",
        }
    context.append_run_log(
        f"stage-recorded stage={stage} elapsed={elapsed}s mode=orchestrator"
    )
    return "ready", {}


def execute_stage(
    context: StageRunContext,
    *,
    handlers: Mapping[str, StageHandler],
    dependencies: StageExecutionDependencies,
) -> tuple[str, dict]:
    """Execute one stage and return (status, stage_result)."""
    stage = context.stage
    handler = handlers.get(stage)
    prompt_mode = handler.prompt_mode if handler is not None else "output_only"

    preflight_ok, preflight_reason = preflight_stage(
        stage=stage,
        plan=context.plan,
        triage_input=context.triage_input,
        dry_run=context.dry_run,
        append_run_log=context.append_run_log,
        validate_reflect_issue_accounting=dependencies.validate_reflect_issue_accounting,
    )
    if not preflight_ok:
        elapsed = int(time.monotonic() - context.stage_start)
        print(
            colorize(
                f"  Stage {stage}: blocked before launch ({preflight_reason}).",
                "red",
            )
        )
        return "failed", {
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": preflight_reason,
        }

    parallel_status, parallel_result, used_parallel = _execute_parallel_stage(
        context=context,
        stage=stage,
        handler=handler,
    )
    if parallel_status != "ready":
        return parallel_status, parallel_result
    if used_parallel:
        return "ready", {}

    prompt, stages_data = _build_subprocess_prompt(
        context=context,
        stage=stage,
        prompt_mode=prompt_mode,
        dependencies=dependencies,
    )
    subprocess_status, subprocess_result, output_file, elapsed = _run_subprocess_stage(
        context=context,
        stage=stage,
        prompt=prompt,
        dependencies=dependencies,
    )
    if subprocess_status != "ready":
        return subprocess_status, subprocess_result

    if output_file is None or elapsed is None:
        return "failed", {
            "status": "failed",
            "error": "subprocess_output_missing",
        }

    record_status, record_result = _record_stage_report_if_needed(
        context=context,
        stage=stage,
        handler=handler,
        dependencies=dependencies,
        output_file=output_file,
        elapsed=elapsed,
        stages_data=stages_data,
    )
    if record_status != "ready":
        return record_status, record_result

    return "ready", {}


__all__ = [
    "DEFAULT_STAGE_HANDLERS",
    "StageExecutionDependencies",
    "StageHandler",
    "build_reflect_repair_prompt",
    "default_stage_execution_dependencies",
    "execute_stage",
    "preflight_stage",
    "read_stage_output",
    "repair_reflect_report_if_needed",
    "stage_report_recorded",
]
