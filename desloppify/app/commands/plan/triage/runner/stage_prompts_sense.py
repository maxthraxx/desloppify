"""Sense-check prompt builders for triage runner."""

from __future__ import annotations

from pathlib import Path

from ..helpers import cluster_issue_ids


def build_sense_check_content_prompt(
    *,
    cluster_name: str,
    plan: dict,
    repo_root: Path,
    policy_block: str = "",
    mode: str = "output_only",
    cli_command: str = "desloppify",
) -> str:
    """Build a content-verification prompt for a single cluster."""
    cluster = plan.get("clusters", {}).get(cluster_name, {})
    steps = cluster.get("action_steps", [])
    issue_ids = cluster_issue_ids(cluster)

    parts: list[str] = []
    parts.append(
        f"You are sense-checking cluster `{cluster_name}` "
        f"({len(steps)} steps, {len(issue_ids)} issues).\n"
        f"Repo root: {repo_root}"
    )

    if mode == "self_record":
        parts.append(
            "## Your job\n"
            "For EVERY step in this cluster, read the actual source file, verify\n"
            "every factual claim, and apply the needed cluster-step updates directly.\n"
        )
    else:
        parts.append(
            "## Your job\n"
            "For EVERY step in this cluster, read the actual source file and verify\n"
            "every factual claim. Then fix anything wrong or vague.\n"
        )

    parts.append(
        "## What to check and fix\n"
        "1. LINE NUMBERS: Does the code at the claimed lines match what the step describes?\n"
        "   Fix: update the line range to match current file state.\n"
        "2. NAMES: Do the function/variable/type names in the step exist in the file?\n"
        "   Fix: correct the names.\n"
        "3. COUNTS: \"Update the 3 imports\" — are there actually 3? Or 5?\n"
        "   Fix: correct the count.\n"
        "4. STALENESS: Is the problem the issue describes still present in the code?\n"
        "   If already fixed, note in your report.\n"
        "5. VAGUENESS: Could a developer with zero context execute this step without\n"
        "   asking a single question? If not:\n"
        "   - Replace \"refactor X\" with the specific transformation\n"
        "   - Replace \"update imports\" with the specific file list\n"
        "   - Replace \"extract into new hook\" with the filename, function signature, return type\n"
        "6. EFFORT TAGS: Does the tag match the actual scope? A one-line rename is \"trivial\",\n"
        "   not \"small\". Decomposing a 400-line file is \"large\", not \"medium\".\n"
        "7. DUPLICATES: If you notice this step does the same thing as a step in another\n"
        "   cluster, note it in your report.\n"
        "8. OVER-ENGINEERING: Would this change make the codebase *worse*? Flag steps that:\n"
        "   - Add abstractions, wrappers, or indirection for a one-time operation\n"
        "   - Introduce unnecessary config, feature flags, or generalization\n"
        "   - Make simple code harder to read for marginal benefit\n"
        "   - Gold-plate beyond what the issue actually requires\n"
        "   - Trade one smell for a worse one (e.g. fix duplication by adding a fragile base class)\n"
        "   If a step is net-negative, recommend removing it or simplifying the approach.\n"
        "   If the entire cluster is net-negative, say so clearly in your report.\n"
    )

    if policy_block:
        parts.append(policy_block)

    if mode == "self_record":
        parts.append(
            "## How to apply fixes\n"
            f"Use the exact CLI prefix: `{cli_command}`\n"
            "1. Inspect current state first:\n"
            f"   `{cli_command} plan cluster show {cluster_name}`\n"
            "2. Apply step corrections directly in this cluster:\n"
            f"   `{cli_command} plan cluster update {cluster_name} --update-step N --detail \"...\" --effort <trivial|small|medium|large> --issue-refs <id...>`\n"
            f"   `{cli_command} plan cluster update {cluster_name} --remove-step N`\n"
            "3. Re-check the cluster after edits:\n"
            f"   `{cli_command} plan cluster show {cluster_name}`\n"
        )
    else:
        parts.append(
            "## How to report fixes\n"
            "Describe the exact step corrections needed, including the corrected detail text,\n"
            "the effort tag, and any stale/duplicate/over-engineered steps that should be removed.\n"
            "The orchestrator will apply the updates.\n"
        )

    if mode == "self_record":
        parts.append(
            "## What NOT to do\n"
            "- Do NOT reorder steps (the structure subagent handles that)\n"
            "- Do NOT add --depends-on (the structure subagent handles that)\n"
            "- Do NOT add new steps for missing cascade updates (the structure subagent handles that)\n"
            "- Do NOT modify any cluster other than the one assigned in this prompt\n"
            "- Do NOT run triage stage commands (`plan triage --stage ...`)\n"
            "- Do NOT debug or repair the CLI / environment\n"
        )
    else:
        parts.append(
            "## What NOT to do\n"
            "- Do NOT reorder steps (the structure subagent handles that)\n"
            "- Do NOT add --depends-on (the structure subagent handles that)\n"
            "- Do NOT add new steps for missing cascade updates (the structure subagent handles that)\n"
            "- Do NOT run any `desloppify` commands\n"
            "- Do NOT debug or repair the CLI / environment\n"
        )

    # Include cluster steps
    parts.append("## Current Steps\n")
    for i, step in enumerate(steps, 1):
        title = step.get("title", str(step)) if isinstance(step, dict) else str(step)
        detail = step.get("detail", "") if isinstance(step, dict) else ""
        effort = step.get("effort", "") if isinstance(step, dict) else ""
        refs = step.get("issue_refs", []) if isinstance(step, dict) else []
        line = f"{i}. **{title}**"
        if effort:
            line += f" [{effort}]"
        if refs:
            line += f" (refs: {', '.join(refs[:3])})"
        if detail:
            line += f"\n   {detail[:300]}"
        parts.append(line)

    if mode == "self_record":
        parts.append(
            "\n## Output\n"
            "Write a plain-text summary of what you verified and what you changed in this cluster."
        )
    else:
        parts.append(
            "\n## Output\n"
            "Write a plain-text report of your findings. The orchestrator records the stage."
        )

    return "\n\n".join(parts)


def build_sense_check_structure_prompt(
    *,
    plan: dict,
    repo_root: Path,
    mode: str = "output_only",
    cli_command: str = "desloppify",
) -> str:
    """Build a structure-verification prompt for cross-cluster dependency checking."""
    clusters = plan.get("clusters", {})

    parts: list[str] = []
    parts.append(
        "You are checking cross-cluster dependencies for the entire triage plan.\n"
        f"Repo root: {repo_root}"
    )

    parts.append(
        "## Your job\n"
        "Build a file-touch graph: for each cluster, which files do its steps reference?\n"
        "Then check for unsafe relationships between clusters.\n"
    )

    parts.append(
        "## What to check and fix\n"
        "1. SHARED FILES: If cluster A and cluster B both have steps touching the same file,\n"
        "   and neither depends on the other → report which dependency edge should be added.\n"
        "2. MISSING CASCADE: If a step renames/removes a function or export, check whether\n"
        "   any other file imports it. If those importers aren't covered by any step in any\n"
        "   cluster → report the cascade step that should be added.\n"
        "   Include the cluster name, affected importers, and issue hash in your report.\n"
        "3. CIRCULAR DEPS: If adding a dependency would create a cycle, flag it in your report\n"
        "   instead of adding it.\n"
    )

    if mode == "self_record":
        parts.append(
            "## How to apply structure fixes\n"
            f"Use the exact CLI prefix: `{cli_command}`\n"
            "Apply only structure-level mutations:\n"
            f"- Add dependency edges: `{cli_command} plan cluster update <name> --depends-on <other-cluster>`\n"
            f"- Add missing cascade steps: `{cli_command} plan cluster update <name> --add-step \"...\" --detail \"...\" --effort <trivial|small|medium|large> --issue-refs <id...>`\n"
        )
        parts.append(
            "## What NOT to do\n"
            "- Do NOT modify existing step detail text (content subagents handled that)\n"
            "- Do NOT change effort tags on existing steps\n"
            "- Do NOT remove existing steps\n"
            "- Do NOT run triage stage commands (`plan triage --stage ...`)\n"
            "- Do NOT debug or repair the CLI / environment\n"
        )
    else:
        parts.append(
            "## What NOT to do\n"
            "- Do NOT modify step detail text (the content subagent handles that)\n"
            "- Do NOT change effort tags (the content subagent handles that)\n"
            "- Do NOT remove steps or deduplicate (the content subagent handles that)\n"
            "- Do NOT run any `desloppify` commands\n"
            "- Do NOT debug or repair the CLI / environment\n"
        )

    # Include all clusters with their steps and dependencies
    parts.append("## Clusters\n")
    for name, c in sorted(clusters.items()):
        if c.get("auto"):
            continue
        steps = c.get("action_steps", [])
        deps = c.get("depends_on_clusters", [])
        issues = cluster_issue_ids(c)
        header = f"### {name} ({len(steps)} steps, {len(issues)} issues)"
        if deps:
            header += f"\n  depends_on: {', '.join(deps)}"
        parts.append(header)
        for i, step in enumerate(steps, 1):
            title = step.get("title", str(step)) if isinstance(step, dict) else str(step)
            detail = step.get("detail", "") if isinstance(step, dict) else ""
            line = f"  {i}. {title}"
            if detail:
                line += f"\n     {detail[:200]}"
            parts.append(line)

    if mode == "self_record":
        parts.append(
            "\n## Output\n"
            "Write a plain-text summary of dependency/cascade fixes you applied."
        )
    else:
        parts.append(
            "\n## Output\n"
            "Write a plain-text report of your findings. The orchestrator records the stage."
        )

    return "\n\n".join(parts)


__all__ = [
    "build_sense_check_content_prompt",
    "build_sense_check_structure_prompt",
]
