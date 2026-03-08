"""Review-oriented shared detector phases (dupes, security, coverage, etc.)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel
from desloppify.base.output.terminal import log
from desloppify.engine.detectors.dupes import detect_duplicates
from desloppify.engine.detectors.jscpd_adapter import detect_with_jscpd
from desloppify.engine.detectors.review_coverage import (
    detect_holistic_review_staleness,
    detect_review_coverage,
)
from desloppify.engine.detectors.security.detector import detect_security_issues
from desloppify.engine.detectors.test_coverage.detector import detect_test_coverage
from desloppify.engine.policy.zones import EXCLUDED_ZONES, filter_entries
from desloppify.languages._framework.base.types import LangRuntimeContract
from desloppify.languages._framework.issue_factories import make_dupe_issues
from desloppify.state import Issue, make_issue

from .shared_phases_helpers import (
    _entries_to_issues,
    _filter_boilerplate_entries_by_zone,
    _find_external_test_files,
    _log_phase_summary,
    _record_detector_coverage,
)


def phase_dupes(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase runner: detect duplicate functions via lang.extract_functions."""
    functions = lang.extract_functions(path)

    if lang.zone_map is not None:
        before = len(functions)
        functions = [
            function
            for function in functions
            if lang.zone_map.get(getattr(function, "file", "")) not in EXCLUDED_ZONES
        ]
        excluded = before - len(functions)
        if excluded:
            log(f"         zones: {excluded} functions excluded (non-production)")

    entries, total_functions = detect_duplicates(functions)
    issues = make_dupe_issues(entries, log)
    return issues, {"dupes": total_functions}


def phase_boilerplate_duplication(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase runner: detect repeated boilerplate code via jscpd."""
    entries = detect_with_jscpd(path)
    if entries is None:
        return [], {}
    entries = _filter_boilerplate_entries_by_zone(entries, lang.zone_map)

    issues: list[Issue] = []
    for entry in entries:
        locations = entry["locations"]
        first = locations[0]
        loc_preview = ", ".join(
            f"{rel(item['file'])}:{item['line']}" for item in locations[:4]
        )
        if len(locations) > 4:
            loc_preview += f", +{len(locations) - 4} more"
        issues.append(
            make_issue(
                "boilerplate_duplication",
                first["file"],
                entry["id"],
                tier=3,
                confidence="medium",
                summary=(
                    f"Boilerplate block repeated across {entry['distinct_files']} files "
                    f"(window {entry['window_size']} lines): {loc_preview}"
                ),
                detail={
                    "distinct_files": entry["distinct_files"],
                    "window_size": entry["window_size"],
                    "locations": locations,
                    "sample": entry["sample"],
                },
            )
        )

    if issues:
        log(f"         boilerplate duplication: {len(issues)} clusters")
    distinct_files = len({loc["file"] for entry in entries for loc in entry["locations"]})
    return issues, {"boilerplate_duplication": distinct_files}


def phase_security(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase: detect security issues (cross-language + lang-specific)."""
    zone_map = lang.zone_map
    files = lang.file_finder(path) if lang.file_finder else []
    entries, cross_lang_scanned = detect_security_issues(
        files,
        zone_map,
        lang.name,
        scan_root=path,
    )
    lang_scanned = 0

    lang_result = lang.detect_lang_security_detailed(files, zone_map)
    lang_entries = lang_result.entries
    lang_scanned = max(0, int(lang_result.files_scanned))
    _record_detector_coverage(lang, lang_result.coverage)
    entries.extend(lang_entries)

    entries = filter_entries(zone_map, entries, "security")
    potential = max(cross_lang_scanned, lang_scanned)

    results = _entries_to_issues(
        "security",
        entries,
        include_zone=True,
        zone_map=zone_map,
    )
    _log_phase_summary("security", results, potential, "files scanned")

    if "security" not in lang.detector_coverage:
        lang.detector_coverage["security"] = {
            "detector": "security",
            "status": "full",
            "confidence": 1.0,
            "summary": "Security coverage complete for enabled detectors.",
            "impact": "",
            "remediation": "",
            "tool": "",
            "reason": "",
        }

    return results, {"security": potential}


def phase_test_coverage(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase: detect test coverage gaps."""
    zone_map = lang.zone_map
    if zone_map is None:
        return [], {}

    graph = lang.dep_graph or lang.build_dep_graph(path)
    extra = _find_external_test_files(path, lang)
    entries, potential = detect_test_coverage(
        graph,
        zone_map,
        lang.name,
        extra_test_files=extra or None,
        complexity_map=lang.complexity_map or None,
    )
    entries = filter_entries(zone_map, entries, "test_coverage")

    results = _entries_to_issues("test_coverage", entries, default_name="")
    _log_phase_summary("test coverage", results, potential, "production files")

    return results, {"test_coverage": potential}


def phase_private_imports(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase: detect cross-module private imports."""
    zone_map = lang.zone_map
    graph = lang.dep_graph or lang.build_dep_graph(path)

    entries, potential = lang.detect_private_imports(graph, zone_map)
    entries = filter_entries(zone_map, entries, "private_imports")

    results = _entries_to_issues("private_imports", entries)
    _log_phase_summary("private imports", results, potential, "files scanned")

    return results, {"private_imports": potential}


def phase_subjective_review(
    path: Path,
    lang: LangRuntimeContract,
) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase: detect files missing subjective design review."""
    zone_map = lang.zone_map
    max_age = lang.review_max_age_days
    files = lang.file_finder(path) if lang.file_finder else []
    review_cache = lang.review_cache
    if isinstance(review_cache, dict) and "files" in review_cache:
        per_file_cache = review_cache.get("files", {})
    else:
        top_level_keys = frozenset({"holistic"})
        raw = review_cache if isinstance(review_cache, dict) else {}
        per_file_cache = {k: v for k, v in raw.items() if k not in top_level_keys}
        review_cache = {"files": per_file_cache}
        if "holistic" in raw:
            review_cache["holistic"] = raw["holistic"]

    entries, potential = detect_review_coverage(
        files,
        zone_map,
        per_file_cache,
        lang.name,
        low_value_pattern=lang.review_low_value_pattern,
        max_age_days=max_age,
        holistic_cache=review_cache.get("holistic") if isinstance(review_cache, dict) else None,
        holistic_total_files=len(files),
    )

    holistic_entries = detect_holistic_review_staleness(
        review_cache,
        total_files=len(files),
        max_age_days=max_age,
    )
    entries.extend(holistic_entries)

    results = _entries_to_issues("subjective_review", entries)
    _log_phase_summary("subjective review", results, potential, "reviewable files")

    return results, {"subjective_review": potential}


def phase_signature(path: Path, lang: LangRuntimeContract) -> tuple[list[Issue], dict[str, int]]:
    """Shared phase runner: detect signature variance via lang.extract_functions."""
    from desloppify.engine.detectors.signature import detect_signature_variance

    functions = lang.extract_functions(path)

    issues: list[Issue] = []
    potentials: dict[str, int] = {}

    if not functions:
        return issues, potentials

    entries, _total = detect_signature_variance(functions, min_occurrences=3)
    for entry in entries:
        issues.append(
            make_issue(
                "signature",
                entry["files"][0],
                f"signature_variance::{entry['name']}",
                tier=3,
                confidence="medium",
                summary=(
                    f"'{entry['name']}' has {entry['signature_count']} different signatures "
                    f"across {entry['file_count']} files"
                ),
            )
        )
    if entries:
        potentials["signature"] = len(entries)
        log(f"         signature variance: {len(entries)}")

    return issues, potentials


__all__ = [
    "phase_boilerplate_duplication",
    "phase_dupes",
    "phase_private_imports",
    "phase_security",
    "phase_signature",
    "phase_subjective_review",
    "phase_test_coverage",
]
