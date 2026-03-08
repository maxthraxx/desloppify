"""Shared detector phase runners reused by language configs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.output.terminal import log
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.state import Issue

from .shared_phases_helpers import (
    _entries_to_issues as _entries_to_issues_impl,
    _filter_boilerplate_entries_by_zone as _filter_boilerplate_entries_by_zone_impl,
    _find_external_test_files,
    _log_phase_summary as _log_phase_summary_impl,
)
from . import shared_phases_review as shared_phases_review_mod
from .shared_phases_review import (
    detect_security_issues as _detect_security_issues_default,
    phase_boilerplate_duplication,
    phase_dupes,
    phase_private_imports,
    phase_signature,
    phase_subjective_review,
    phase_test_coverage,
)
from .shared_phases_structural import (
    make_structural_coupling_phase_pair,
    run_coupling_phase,
    run_structural_phase,
)

detect_security_issues = _detect_security_issues_default


def find_external_test_files(path: Path, lang: LangRuntimeContract) -> set[str]:
    """Compatibility wrapper with patchable get_project_root dependency."""
    return _find_external_test_files(path, lang, get_project_root_fn=get_project_root)


def _entries_to_issues(
    detector: str,
    entries: list[dict[str, Any]],
    *,
    default_name: str = "",
    include_zone: bool = False,
    zone_map=None,
) -> list[Issue]:
    """Compatibility wrapper for entry->issue normalization."""
    return _entries_to_issues_impl(
        detector,
        entries,
        default_name=default_name,
        include_zone=include_zone,
        zone_map=zone_map,
    )


def _filter_boilerplate_entries_by_zone(
    entries: list[dict[str, Any]],
    zone_map,
) -> list[dict[str, Any]]:
    """Compatibility wrapper for boilerplate zone filtering."""
    return _filter_boilerplate_entries_by_zone_impl(entries, zone_map)


def _log_phase_summary(label: str, results: list[Issue], potential: int, unit: str) -> None:
    """Compatibility wrapper with patchable module-level logger."""
    _log_phase_summary_impl(label, results, potential, unit, log_fn=log)


def phase_security(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    """Compatibility wrapper with patchable security detector dependency."""
    original = shared_phases_review_mod.detect_security_issues
    shared_phases_review_mod.detect_security_issues = detect_security_issues
    try:
        return shared_phases_review_mod.phase_security(path, lang)
    finally:
        shared_phases_review_mod.detect_security_issues = original


__all__ = [
    "_filter_boilerplate_entries_by_zone",
    "detect_security_issues",
    "find_external_test_files",
    "make_structural_coupling_phase_pair",
    "phase_boilerplate_duplication",
    "phase_dupes",
    "phase_private_imports",
    "phase_security",
    "phase_signature",
    "phase_subjective_review",
    "phase_test_coverage",
    "run_coupling_phase",
    "run_structural_phase",
]
