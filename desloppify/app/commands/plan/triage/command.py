"""Handler for `plan triage` command entrypoint."""

from __future__ import annotations

import argparse

from desloppify.app.commands.helpers.state import require_issue_inventory
from . import helpers as _helpers_mod
from .services import default_triage_services
from . import workflow as _workflow_mod

_triage_coverage = _helpers_mod.triage_coverage

def cmd_plan_triage(args: argparse.Namespace) -> None:
    """Run staged triage workflow: observe -> reflect -> organize -> enrich -> sense-check -> commit."""
    resolved_services = default_triage_services()
    _workflow_mod.run_triage_workflow(
        args,
        services=resolved_services,
        require_issue_inventory_fn=require_issue_inventory,
    )


__all__ = [
    "_triage_coverage",
    "cmd_plan_triage",
]
