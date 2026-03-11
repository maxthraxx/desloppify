"""Prompt/data contracts for whole-plan triage orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from desloppify.engine._plan.schema import (
    Cluster,
    PlanModel,
    ensure_plan_defaults,
    triage_clusters,
)
from desloppify.engine._state.schema import StateModel


@dataclass
class TriageInput:
    """All data needed to produce/update triage clusters."""

    open_issues: dict[str, dict]       # id -> issue (review + concerns)
    mechanical_issues: dict[str, dict]  # id -> issue (non-review, for context)
    existing_epics: dict[str, Cluster]    # compatibility alias; values are clusters
    dimension_scores: dict[str, Any]      # for context
    new_since_last: set[str]             # issue IDs new since last triage
    resolved_since_last: set[str]        # issue IDs resolved since last
    previously_dismissed: list[str]      # IDs dismissed in prior triage
    triage_version: int                  # next version number
    resolved_issues: dict[str, dict]   # full issue objects for resolved IDs
    completed_clusters: list[dict]       # clusters completed since last triage

    @property
    def existing_clusters(self) -> dict[str, Cluster]:
        """Canonical cluster view used by staged triage docs."""
        return self.existing_epics

@dataclass
class DismissedIssue:
    """A issue the LLM says doesn't make sense."""

    issue_id: str
    reason: str

@dataclass
class ContradictionNote:
    """Record of a resolved contradiction."""

    kept: str
    dismissed: str
    reason: str

@dataclass
class TriageResult:
    """Parsed and validated LLM triage output."""

    strategy_summary: str
    epics: list[dict]
    dismissed_issues: list[DismissedIssue] = field(default_factory=list)
    contradiction_notes: list[ContradictionNote] = field(default_factory=list)
    priority_rationale: str = ""

    @property
    def clusters(self) -> list[dict]:
        """Canonical cluster view for triage results."""
        return self.epics


def _issue_dimension(issue: dict) -> str:
    detail = issue.get("detail", {})
    if isinstance(detail, dict):
        dimension = detail.get("dimension", "")
        if isinstance(dimension, str):
            return dimension
    return ""


def _recurring_dimensions(
    open_issues: dict[str, dict],
    resolved_issues: dict[str, dict],
) -> dict[str, dict[str, list[str]]]:
    open_by_dim: dict[str, list[str]] = {}
    for issue_id, issue in open_issues.items():
        dimension = _issue_dimension(issue)
        if dimension:
            open_by_dim.setdefault(dimension, []).append(issue_id)

    resolved_by_dim: dict[str, list[str]] = {}
    for issue_id, issue in resolved_issues.items():
        dimension = _issue_dimension(issue)
        if dimension:
            resolved_by_dim.setdefault(dimension, []).append(issue_id)

    recurring: dict[str, dict[str, list[str]]] = {}
    for dimension in sorted(set(open_by_dim) & set(resolved_by_dim)):
        recurring[dimension] = {
            "open": sorted(open_by_dim[dimension]),
            "resolved": sorted(resolved_by_dim[dimension]),
        }
    return recurring

def collect_triage_input(plan: PlanModel, state: StateModel) -> TriageInput:
    """Gather all data needed for the triage LLM prompt."""
    ensure_plan_defaults(plan)
    issues = state.get("issues", {})
    meta = plan.get("epic_triage_meta", {})
    epics = triage_clusters(plan)

    open_review: dict[str, dict] = {}
    open_mechanical: dict[str, dict] = {}
    for fid, f in issues.items():
        if f.get("status") != "open":
            continue
        if f.get("detector") in ("review", "concerns"):
            open_review[fid] = f
        else:
            open_mechanical[fid] = f

    triaged_ids = set(meta.get("triaged_ids", []))
    current_review_ids = set(open_review.keys())
    new_since = current_review_ids - triaged_ids
    resolved_since = triaged_ids - current_review_ids
    previously_dismissed = list(meta.get("dismissed_ids", []))
    version = int(meta.get("version", 0)) + 1

    # Resolved issue objects (for REFLECT stage)
    resolved_issue_objs = {
        fid: issues[fid] for fid in resolved_since if fid in issues
    }

    # Completed clusters since last triage completion
    last_completed = meta.get("last_completed_at", "")
    all_completed: list[dict] = plan.get("completed_clusters", [])
    if last_completed:
        recent_completed = [
            c for c in all_completed
            if c.get("completed_at", "") > last_completed
        ]
    else:
        recent_completed = list(all_completed)

    return TriageInput(
        open_issues=open_review,
        mechanical_issues=open_mechanical,
        existing_epics=dict(epics),
        dimension_scores=state.get("dimension_scores", {}),
        new_since_last=new_since,
        resolved_since_last=resolved_since,
        previously_dismissed=previously_dismissed,
        triage_version=version,
        resolved_issues=resolved_issue_objs,
        completed_clusters=recent_completed,
    )

_TRIAGE_SYSTEM_PROMPT = """\
You are maintaining the meta-plan for this codebase. Produce a coherent
prioritized strategy for all open review issues.

Your plan should:
- Cluster issues by ROOT CAUSE, not by dimension or detector
- Give each cluster a clear thesis: one imperative sentence
- Order clusters by dependency: what must be done first for later work to make sense
- Dismiss issues that don't make sense, are contradictory, or are false positives
- Mark which clusters are agent-safe (can be executed mechanically) vs need human judgment
- Avoid creating work that contradicts other work in the plan
- Be ambitious but realistic — aim to resolve all issues coherently

Available directions for clusters: delete, merge, flatten, enforce, simplify, decompose, extract, inline.

Available plan tools (the agent executing your plan has access to these):
- `desloppify plan queue` — view the execution queue in priority order
- `desloppify backlog` — inspect broader open work outside the execution queue
- `desloppify plan focus <name>` — focus the queue on one cluster
- `desloppify plan skip <id> --permanent --note "why" --attest "..."` — permanently dismiss
- `desloppify plan skip <id> --note "revisit later"` — temporarily defer
- `desloppify plan resolve <id> --note "what I did" --attest "..."` — mark resolved
- `desloppify plan reorder <id> top|bottom|before|after <target>` — reorder
- `desloppify plan cluster show <name>` — inspect a cluster
- `desloppify scan` — re-scan after making changes to verify progress
- `desloppify show review --status open` — see all open review issues

Your output defines the ENTIRE work plan. Issues not assigned to any cluster
will remain in the queue as individual items. Dismissed issues will be
removed from the queue with your stated reason.

Respond with a single JSON object matching this schema:
{
  "strategy_summary": "2-4 sentence narrative: what the meta-plan says, top priorities, current state",
  "clusters": [
    {
      "name": "slug-name",
      "thesis": "imperative one-liner",
      "direction": "delete|merge|flatten|enforce|simplify|decompose|extract|inline",
      "root_cause": "why this cluster exists",
      "issue_ids": ["id1", "id2"],
      "dismissed": ["id3"],
      "agent_safe": true,
      "dependency_order": 1,
      "action_steps": [
        {
          "title": "Short imperative step title",
          "detail": "Concrete implementation detail with file paths/locations",
          "issue_refs": ["id1"]
        }
      ],
      "status": "pending"
    }
  ],
  "dismissed_issues": [
    {"issue_id": "id", "reason": "why this issue doesn't make sense"}
  ],
  "contradiction_notes": [
    {"kept": "issue_id", "dismissed": "issue_id", "reason": "why"}
  ],
  "priority_rationale": "why the dependency_order is what it is"
}
"""

def build_triage_prompt(si: TriageInput) -> str:
    """Build the user-facing prompt content with all issue data."""
    parts: list[str] = []

    if si.existing_clusters:
        parts.append("## Existing clusters")
        for name, cluster in sorted(si.existing_clusters.items()):
            status = cluster.get("status", "pending")
            thesis = cluster.get("thesis", "")
            direction = cluster.get("direction", "")
            fids = cluster.get("issue_ids", [])
            parts.append(
                f"- {name} [{status}] ({direction}): {thesis}"
                f"\n  Issues: {', '.join(fids[:10])}"
                f"{'...' if len(fids) > 10 else ''}"
            )
        parts.append("")

    # Section: what changed
    if si.new_since_last:
        parts.append(f"## New issues since last triage ({len(si.new_since_last)})")
        for fid in sorted(si.new_since_last):
            f = si.open_issues.get(fid, {})
            parts.append(f"- {fid}: {f.get('summary', '(no summary)')}")
        parts.append("")

    if si.resolved_since_last:
        parts.append(f"## Resolved since last triage ({len(si.resolved_since_last)})")
        for fid in sorted(si.resolved_since_last):
            parts.append(f"- {fid}")
        parts.append("")

    if si.resolved_issues:
        parts.append(
            "## Resolved review issues available for recurrence context "
            f"({len(si.resolved_issues)})"
        )
        resolved_ids = sorted(si.resolved_issues)
        for fid in resolved_ids[:30]:
            issue = si.resolved_issues.get(fid, {})
            summary = str(issue.get("summary", "(no summary)"))
            dimension = _issue_dimension(issue)
            dim_suffix = f" [{dimension}]" if dimension else ""
            parts.append(f"- {fid}{dim_suffix}: {summary}")
        if len(resolved_ids) > 30:
            parts.append(f"- ... and {len(resolved_ids) - 30} more resolved issues")
        parts.append("")

    if si.completed_clusters:
        parts.append(
            "## Completed clusters since last triage "
            f"({len(si.completed_clusters)})"
        )
        completed = sorted(
            si.completed_clusters,
            key=lambda cluster: str(cluster.get("completed_at", "")),
            reverse=True,
        )
        for cluster in completed[:10]:
            name = str(cluster.get("name", "(unnamed cluster)"))
            thesis = str(cluster.get("thesis") or cluster.get("description") or "")
            completed_at = str(cluster.get("completed_at", ""))
            issue_ids = cluster.get("issue_ids", [])
            issue_count = len(issue_ids) if isinstance(issue_ids, list) else 0
            parts.append(
                f"- {name}: {thesis} (issues: {issue_count}, completed_at: {completed_at})"
            )
        if len(completed) > 10:
            parts.append(f"- ... and {len(completed) - 10} more completed clusters")
        parts.append("")

    recurring = _recurring_dimensions(si.open_issues, si.resolved_issues)
    if recurring:
        parts.append(
            "## Potential recurring dimensions (resolved issues still have open peers)"
        )
        for dimension, bucket in recurring.items():
            open_count = len(bucket["open"])
            resolved_count = len(bucket["resolved"])
            parts.append(
                f"- {dimension}: {open_count} open / {resolved_count} recently resolved"
            )
        parts.append("")

    # Section: all open review issues
    parts.append(f"## All open review issues ({len(si.open_issues)})")
    for fid, f in sorted(si.open_issues.items()):
        detail = f.get("detail", {}) if isinstance(f.get("detail"), dict) else {}
        suggestion = detail.get("suggestion", "")
        dimension = detail.get("dimension", "")
        confidence = f.get("confidence", "medium")
        file_path = f.get("file", "")
        summary = f.get("summary", "")
        parts.append(f"- [{confidence}] {fid}")
        parts.append(f"  File: {file_path}")
        if dimension:
            parts.append(f"  Dimension: {dimension}")
        parts.append(f"  Summary: {summary}")
        if suggestion:
            parts.append(f"  Suggestion: {suggestion}")
    parts.append("")

    # Section: dimension scores for context
    if si.dimension_scores:
        parts.append("## Dimension scores (context)")
        for name, data in sorted(si.dimension_scores.items()):
            if isinstance(data, dict):
                score = data.get("score", "?")
                strict = data.get("strict", score)
                issues = data.get("failing", 0)
                parts.append(f"- {name}: {score}% (strict: {strict}%, {issues} issues)")
        parts.append("")

    # Section: previously dismissed
    if si.previously_dismissed:
        parts.append(f"## Previously dismissed ({len(si.previously_dismissed)})")
        parts.append("Maintain unless contradicted by new evidence.")
        for fid in si.previously_dismissed:
            parts.append(f"- {fid}")
        parts.append("")

    return "\n".join(parts)

__all__ = [
    "_TRIAGE_SYSTEM_PROMPT",
    "ContradictionNote",
    "DismissedIssue",
    "TriageInput",
    "TriageResult",
    "build_triage_prompt",
    "collect_triage_input",
]
