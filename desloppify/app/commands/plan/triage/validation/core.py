"""Validation and guardrail helpers for triage stage workflow."""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from desloppify.app.commands.helpers.command_runtime import command_runtime
from desloppify.base.output.terminal import colorize
from desloppify.engine.plan_state import save_plan
from desloppify.engine.plan_triage import collect_triage_input, detect_recurring_patterns
from desloppify.state_io import utc_now

from ..helpers import (
    cluster_issue_ids,
    manual_clusters_with_issues,
)
from ..stages.helpers import unclustered_review_issues, unenriched_clusters
from .completion_policy import (
    _completion_clusters_valid,
    _completion_strategy_valid,
    _confirm_existing_stages_valid,
    _confirm_note_valid,
    _confirm_strategy_valid,
    _confirmed_text_or_error,
    _note_cites_new_issues_or_error,
    _require_prior_strategy_for_confirm,
    _resolve_completion_strategy,
    _resolve_confirm_existing_strategy,
)
from .completion_stages import (
    _auto_confirm_enrich_for_complete,
    _auto_confirm_stage_for_complete,
    _require_enrich_stage_for_complete,
    _require_organize_stage_for_complete,
    _require_sense_check_stage_for_complete,
)
from .enrich_checks import (
    _cluster_file_overlaps,
    _clusters_with_directory_scatter,
    _clusters_with_high_step_ratio,
    _enrich_report_or_error,
    _require_organize_stage_for_enrich,
    _steps_missing_issue_refs,
    _steps_referencing_skipped_issues,
    _steps_with_bad_paths,
    _steps_with_vague_detail,
    _steps_without_effort,
    _underspecified_steps,
)
from .reflect_accounting import (
    ReflectDisposition,
    analyze_reflect_issue_accounting,
    parse_reflect_dispositions,
    validate_reflect_accounting,
)
from .stage_policy import (
    AutoConfirmStageRequest,
    ReflectAutoConfirmDeps,
    StagePrerequisite,
    auto_confirm_observe_if_attested,
    auto_confirm_reflect_for_organize,
    confirm_stage,
    missing_stage_prerequisite,
    require_prerequisite,
)

_missing_stage_prerequisite = missing_stage_prerequisite
require_stage_prerequisite = require_prerequisite
_analyze_reflect_issue_accounting = analyze_reflect_issue_accounting
_validate_reflect_issue_accounting = validate_reflect_accounting

RecurringDimensionBuckets = dict[str, dict[str, list[str]]]


def _auto_confirm_stage(
    *,
    plan: dict,
    stage_record: dict,
    attestation: str | None,
    request: AutoConfirmStageRequest,
    save_plan_fn: Callable[..., None] | None = None,
    utc_now_fn: Callable[[], str] | None = None,
) -> bool:
    return confirm_stage(
        plan=plan,
        stage_record=stage_record,
        attestation=attestation,
        request=request,
        save_plan_fn=save_plan_fn,
        utc_now_fn=utc_now_fn,
    )


def _auto_confirm_observe_if_attested(
    *,
    plan: dict,
    stages: dict,
    attestation: str | None,
    triage_input,
) -> bool:
    return auto_confirm_observe_if_attested(
        plan=plan,
        stages=stages,
        attestation=attestation,
        triage_input=triage_input,
        save_plan_fn=save_plan,
        utc_now_fn=utc_now,
    )


def _auto_confirm_reflect_for_organize(
    *,
    args,
    plan: dict,
    stages: dict,
    attestation: str | None,
    deps: ReflectAutoConfirmDeps | None = None,
) -> bool:
    resolved_deps = deps or ReflectAutoConfirmDeps()
    wrapped_deps = ReflectAutoConfirmDeps(
        triage_input=resolved_deps.triage_input,
        command_runtime_fn=resolved_deps.command_runtime_fn or command_runtime,
        collect_triage_input_fn=resolved_deps.collect_triage_input_fn or collect_triage_input,
        detect_recurring_patterns_fn=(
            resolved_deps.detect_recurring_patterns_fn or detect_recurring_patterns
        ),
        save_plan_fn=resolved_deps.save_plan_fn or save_plan,
    )
    return auto_confirm_reflect_for_organize(
        args=args,
        plan=plan,
        stages=stages,
        attestation=attestation,
        deps=wrapped_deps,
        utc_now_fn=utc_now,
    )


def _validate_recurring_dimension_mentions(
    *,
    report: str,
    recurring_dims: list[str],
    recurring: RecurringDimensionBuckets,
) -> bool:
    if not recurring_dims:
        return True
    report_lower = report.lower()
    mentioned = [dim for dim in recurring_dims if dim.lower() in report_lower]
    if mentioned:
        return True
    print(colorize("  Recurring patterns detected but not addressed in report:", "red"))
    for dim in recurring_dims:
        info = recurring[dim]
        print(
            colorize(
                f"    {dim}: {len(info['resolved'])} resolved, "
                f"{len(info['open'])} still open — potential loop",
                "yellow",
            )
        )
    print(colorize("  Your report must mention at least one recurring dimension name.", "dim"))
    return False


# ---------------------------------------------------------------------------
# Disposition types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActualDisposition:
    """What actually happened to an issue in plan state."""

    kind: Literal["clustered", "skipped", "unplaced"]
    cluster_name: str = ""  # non-empty when kind == "clustered"

    def describe(self, intended: ReflectDisposition | None = None) -> str:
        """Human-readable description for error messages."""
        if self.kind == "skipped":
            return "permanently skipped"
        if self.kind == "clustered":
            if intended and intended.decision == "cluster" and self.cluster_name != intended.target:
                return f'in cluster "{self.cluster_name}" (expected "{intended.target}")'
            return f'clustered in "{self.cluster_name}"'
        if self.kind == "unplaced":
            if intended and intended.decision == "cluster":
                return "not in any cluster"
            return "not skipped, not clustered"
        return "unknown state"


def _build_actual_disposition_index(plan: dict) -> dict[str, ActualDisposition]:
    """Build a lookup from issue ID to its actual disposition in plan state."""
    index: dict[str, ActualDisposition] = {}

    for cluster_name, cluster in plan.get("clusters", {}).items():
        if cluster.get("auto"):
            continue
        for fid in cluster_issue_ids(cluster):
            index[fid] = ActualDisposition(kind="clustered", cluster_name=cluster_name)

    for fid in (plan.get("skipped", {}) or {}):
        if isinstance(fid, str):
            index[fid] = ActualDisposition(kind="skipped")

    return index


def _require_reflect_stage_for_organize(stages: dict) -> bool:
    return require_stage_prerequisite(
        stages,
        flow="organize",
        messages={
            "observe": (
                "  Cannot organize: observe stage not complete.",
                '  Run: desloppify plan triage --stage observe --report "..."',
            ),
            "reflect": (
                "  Cannot organize: reflect stage not complete.",
                '  Run: desloppify plan triage --stage reflect --report "..."',
            ),
        },
    )


def _manual_clusters_or_error(
    plan: dict,
    *,
    open_review_ids: set[str] | None = None,
) -> list[str] | None:
    manual_clusters = manual_clusters_with_issues(plan)
    if manual_clusters:
        return manual_clusters
    if open_review_ids is not None and not open_review_ids:
        return []
    any_clusters = [
        name for name, cluster in plan.get("clusters", {}).items()
        if cluster_issue_ids(cluster)
    ]
    if any_clusters:
        print(colorize("  Cannot organize: only auto-clusters exist.", "red"))
        print(colorize("  Create manual clusters that group issues by root cause:", "dim"))
    else:
        print(colorize("  Cannot organize: no clusters with issues exist.", "red"))
    print(colorize('    desloppify plan cluster create <name> --description "..."', "dim"))
    print(colorize("    desloppify plan cluster add <name> <issue-patterns>", "dim"))
    return None


def _clusters_enriched_or_error(plan: dict) -> bool:
    gaps = unenriched_clusters(plan)
    if not gaps:
        return True
    print(colorize(f"  Cannot organize: {len(gaps)} cluster(s) need enrichment.", "red"))
    for name, missing in gaps:
        print(colorize(f"    {name}: missing {', '.join(missing)}", "yellow"))
    print()
    print(colorize("  Each cluster needs a description and action steps:", "dim"))
    print(colorize('    desloppify plan cluster update <name> --description "what this cluster addresses" --steps "step 1" "step 2"', "dim"))
    return False


def _unclustered_review_issues_or_error(plan: dict, state: dict) -> bool:
    """Block if open review issues aren't in any manual cluster. Return True if OK."""
    unclustered = unclustered_review_issues(plan, state)
    if not unclustered:
        return True
    print(colorize(f"  Cannot organize: {len(unclustered)} review issue(s) have no cluster.", "red"))
    for fid in unclustered[:10]:
        short = fid.rsplit("::", 2)[-2] if "::" in fid else fid
        print(colorize(f"    {short}", "yellow"))
    if len(unclustered) > 10:
        print(colorize(f"    ... and {len(unclustered) - 10} more", "yellow"))
    print()
    print(colorize("  Every review issue needs an action plan. Either:", "dim"))
    print(colorize("    1. Add to a cluster: desloppify plan cluster add <name> <pattern>", "dim"))
    print(colorize('    2. Wontfix it: desloppify plan skip --permanent <pattern> --note "reason" --attest "..."', "dim"))
    return False


def _organize_report_or_error(report: str | None) -> str | None:
    if not report:
        print(colorize("  --report is required for --stage organize.", "red"))
        print(colorize("  Summarize your prioritized organization:", "dim"))
        print(colorize("  - Did you defer contradictory issues before clustering?", "dim"))
        print(colorize("  - What clusters did you create and why?", "dim"))
        print(colorize("  - Explicit priority ordering: which cluster 1st, 2nd, 3rd and why?", "dim"))
        print(colorize("  - What depends on what? What unblocks the most?", "dim"))
        return None
    if len(report) < 100:
        print(colorize(f"  Report too short: {len(report)} chars (minimum 100).", "red"))
        print(colorize("  Explain what you organized, your priorities, and focus order.", "dim"))
        return None
    return report


@dataclass(frozen=True)
class LedgerMismatch:
    """One issue where plan state diverges from the reflect disposition."""

    intended: ReflectDisposition
    actual: ActualDisposition

    @property
    def issue_id(self) -> str:
        return self.intended.issue_id

    @property
    def expected_decision(self) -> str:
        return self.intended.decision

    @property
    def expected_target(self) -> str:
        return self.intended.target

    @property
    def actual_state(self) -> str:
        return self.actual.describe(self.intended)


def _disposition_matches(intended: ReflectDisposition, actual: ActualDisposition) -> bool:
    """True when the actual plan state satisfies the intended disposition."""
    if intended.decision == "permanent_skip":
        return actual.kind == "skipped"
    if intended.decision == "cluster":
        return actual.kind == "clustered" and actual.cluster_name == intended.target
    return False


def validate_organize_against_reflect_ledger(
    *,
    plan: dict,
    stages: dict,
) -> list[LedgerMismatch]:
    """Check that plan mutations match the reflect disposition ledger.

    Returns an empty list when all dispositions are faithfully materialized,
    or a list of mismatches when organize diverged from the reflect plan.

    Silently returns [] for legacy runs without a disposition_ledger.
    """
    raw_ledger = stages.get("reflect", {}).get("disposition_ledger")
    if not raw_ledger:
        return []

    ledger = [ReflectDisposition.from_dict(d) for d in raw_ledger]
    actuals = _build_actual_disposition_index(plan)
    unplaced = ActualDisposition(kind="unplaced")

    return [
        LedgerMismatch(intended=d, actual=actuals.get(d.issue_id, unplaced))
        for d in ledger
        if not _disposition_matches(d, actuals.get(d.issue_id, unplaced))
    ]


def _validate_organize_against_ledger_or_error(
    *,
    plan: dict,
    stages: dict,
) -> bool:
    """Block organize if plan state diverges from the reflect disposition ledger."""
    mismatches = validate_organize_against_reflect_ledger(
        plan=plan, stages=stages,
    )
    if not mismatches:
        return True

    print(
        colorize(
            f"  Cannot organize: {len(mismatches)} issue(s) diverge from the reflect plan.",
            "red",
        )
    )
    for m in mismatches[:10]:
        short_id = m.issue_id.rsplit("::", 1)[-1]
        print(
            colorize(
                f"    {short_id}: reflect said {m.expected_decision} "
                f'"{m.expected_target}", but {m.actual_state}',
                "yellow",
            )
        )
    if len(mismatches) > 10:
        print(colorize(f"    ... and {len(mismatches) - 10} more", "yellow"))
    print()
    print(
        colorize(
            "  Fix the plan to match the reflect ledger, or re-run reflect to update dispositions.",
            "dim",
        )
    )
    return False


__all__ = [
    "_auto_confirm_enrich_for_complete",
    "_auto_confirm_observe_if_attested",
    "AutoConfirmStageRequest",
    "_auto_confirm_stage_for_complete",
    "_auto_confirm_reflect_for_organize",
    "_cluster_file_overlaps",
    "_clusters_with_directory_scatter",
    "_clusters_with_high_step_ratio",
    "_clusters_enriched_or_error",
    "_enrich_report_or_error",
    "_unclustered_review_issues_or_error",
    "_validate_reflect_issue_accounting",
    "_completion_clusters_valid",
    "_completion_strategy_valid",
    "_confirm_existing_stages_valid",
    "_confirm_note_valid",
    "_confirm_strategy_valid",
    "_confirmed_text_or_error",
    "_manual_clusters_or_error",
    "_note_cites_new_issues_or_error",
    "_organize_report_or_error",
    "_require_enrich_stage_for_complete",
    "_require_organize_stage_for_complete",
    "_require_organize_stage_for_enrich",
    "_require_prior_strategy_for_confirm",
    "_require_reflect_stage_for_organize",
    "_require_sense_check_stage_for_complete",
    "_missing_stage_prerequisite",
    "_resolve_completion_strategy",
    "_resolve_confirm_existing_strategy",
    "_underspecified_steps",
    "_steps_missing_issue_refs",
    "_steps_referencing_skipped_issues",
    "_steps_with_bad_paths",
    "_steps_with_vague_detail",
    "_steps_without_effort",
    "_validate_organize_against_ledger_or_error",
    "_validate_recurring_dimension_mentions",
    "LedgerMismatch",
    "ReflectDisposition",
    "parse_reflect_dispositions",
    "require_stage_prerequisite",
    "validate_organize_against_reflect_ledger",
]
