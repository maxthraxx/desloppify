"""Abstraction-economy scoring internals for holistic context budgets."""

from __future__ import annotations

import ast
import dataclasses
import re
from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.intelligence.review.context import file_excerpt

from .budget_analysis import (
    _count_signature_params,
    _extract_type_names,
    _score_clamped,
)
from .budget_patterns_enums import (
    _census_type_strategies,
    _collect_enum_defs,
    _find_enum_bypass,
)
from .budget_patterns_types import (
    _collect_typed_dict_defs,
    _find_dict_any_annotations,
    _find_typed_dict_usage_violations,
)
from .budget_patterns_wrappers import (
    _find_delegation_heavy_classes,
    _find_facade_modules,
    _find_python_passthrough_wrappers,
)

_DEF_SIGNATURE_RE = re.compile(
    r"(?:^|\n)\s*(?:async\s+def|def|async\s+function|function)\s+\w+\s*\(([^)]*)\)",
    re.MULTILINE,
)

_TS_PASSTHROUGH_RE = re.compile(
    r"\bfunction\s+(\w+)\s*\([^)]*\)\s*\{\s*return\s+(\w+)\s*\(",
    re.MULTILINE,
)

_INTERFACE_RE = re.compile(
    r"\binterface\s+([A-Za-z_]\w*)\b|\bclass\s+([A-Za-z_]\w*Protocol)\b"
)

_IMPLEMENTS_RE = re.compile(r"\bclass\s+\w+\s+implements\s+([^{:\n]+)")

_INHERITS_RE = re.compile(r"\bclass\s+\w+\s*(?:\(([^)\n]+)\)\s*:|:\s*([^\n{]+))")

_CHAIN_RE = re.compile(r"\b(?:\w+\.){2,}\w+\b")

_CONFIG_BAG_RE = re.compile(
    r"\b(?:config|configs|options|opts|params|ctx|context)\b",
    re.IGNORECASE,
)


def _compute_sub_axes(
    *,
    wrapper_rate: float,
    util_files: list,
    indirection_hotspots: list,
    wide_param_bags: list,
    one_impl_interfaces: list,
    delegation_classes: list,
    facade_modules: list,
    typed_dict_violation_files: set,
    total_typed_dict_violations: int,
    dict_any_count: int = 0,
    enum_bypass_count: int = 0,
) -> dict[str, float]:
    """Compute all 6 sub-axis scores for the abstractions dimension."""
    abstraction_leverage = _score_clamped(
        100 - (wrapper_rate * 120) - (len(util_files) * 1.5)
    )
    indirection_cost = _score_clamped(
        100
        - (sum(item["max_chain_depth"] for item in indirection_hotspots[:20]) * 2.5)
        - (sum(item["wide_functions"] for item in wide_param_bags[:20]) * 2.0)
    )
    interface_honesty = _score_clamped(100 - (len(one_impl_interfaces) * 8))

    top10_delegation = delegation_classes[:10]
    avg_delegation_ratio = (
        sum(d["delegation_ratio"] for d in top10_delegation) / len(top10_delegation)
        if top10_delegation
        else 0.0
    )
    delegation_density = _score_clamped(
        100 - (avg_delegation_ratio * 80) - (len(delegation_classes) * 5)
    )
    avg_facade_ratio = (
        sum(f["re_export_ratio"] for f in facade_modules[:10]) / len(facade_modules[:10])
        if facade_modules
        else 0.0
    )
    definition_directness = _score_clamped(
        100 - (len(facade_modules) * 8) - (avg_facade_ratio * 50)
    )
    type_discipline = _score_clamped(
        100
        - (len(typed_dict_violation_files) * 6)
        - (total_typed_dict_violations * 1.5)
        - (dict_any_count * 1.0)
        - (enum_bypass_count * 2.0)
    )
    return {
        "abstraction_leverage": abstraction_leverage,
        "indirection_cost": indirection_cost,
        "interface_honesty": interface_honesty,
        "delegation_density": delegation_density,
        "definition_directness": definition_directness,
        "type_discipline": type_discipline,
    }


def _build_abstraction_leverage_context(
    *,
    util_files: list[dict],
    wrappers_by_file: list[dict[str, object]],
) -> dict[str, object]:
    context: dict[str, object] = {}
    if util_files:
        context["util_files"] = sorted(util_files, key=lambda item: -item["loc"])[:20]
    if wrappers_by_file:
        context["pass_through_wrappers"] = wrappers_by_file[:20]
    return context


def _build_indirection_cost_context(
    *,
    indirection_hotspots: list[dict[str, object]],
    wide_param_bags: list[dict[str, object]],
) -> dict[str, object]:
    context: dict[str, object] = {}
    if indirection_hotspots:
        context["indirection_hotspots"] = indirection_hotspots[:20]
    if wide_param_bags:
        context["wide_param_bags"] = wide_param_bags[:20]
    return context


def _build_interface_honesty_context(
    *,
    one_impl_interfaces: list[dict[str, object]],
) -> dict[str, object]:
    if one_impl_interfaces:
        return {"one_impl_interfaces": one_impl_interfaces[:20]}
    return {}


def _build_delegation_density_context(
    *,
    delegation_classes: list[dict],
) -> dict[str, object]:
    if delegation_classes:
        return {"delegation_heavy_classes": delegation_classes}
    return {}


def _build_definition_directness_context(
    *,
    facade_modules: list[dict],
) -> dict[str, object]:
    if facade_modules:
        return {"facade_modules": facade_modules}
    return {}


def _build_type_discipline_context(
    *,
    typed_dict_violations: list[dict],
    dict_any_annotations: list[dict] | None = None,
    enum_bypass_patterns: list[dict] | None = None,
    type_strategy_census: dict[str, list[dict]] | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {}
    if typed_dict_violations:
        context["typed_dict_violations"] = typed_dict_violations
    if dict_any_annotations:
        context["dict_any_annotations"] = dict_any_annotations
    if enum_bypass_patterns:
        context["enum_bypass_patterns"] = enum_bypass_patterns
    if type_strategy_census:
        context["type_strategy_census"] = {
            strategy: len(items)
            for strategy, items in type_strategy_census.items()
        }
    return context


def _assemble_context(
    *,
    util_files: list,
    wrapper_rate: float,
    total_wrappers: int,
    total_function_signatures: int,
    wrappers_by_file: list,
    one_impl_interfaces: list,
    indirection_hotspots: list,
    wide_param_bags: list,
    delegation_classes: list,
    facade_modules: list,
    typed_dict_violations: list,
    total_typed_dict_violations: int,
    sub_axes: dict[str, float],
    dict_any_annotations: list | None = None,
    enum_bypass_patterns: list | None = None,
    type_strategy_census: dict | None = None,
) -> dict:
    """Build the final context dict from collected data and sub-axis scores."""
    util_list = sorted(util_files, key=lambda item: -item["loc"])[:20]
    context: dict[str, object] = {
        "util_files": util_list,
        "summary": {
            "wrapper_rate": round(wrapper_rate, 3),
            "total_wrappers": total_wrappers,
            "total_function_signatures": total_function_signatures,
            "one_impl_interface_count": len(one_impl_interfaces),
            "indirection_hotspot_count": len(indirection_hotspots),
            "wide_param_bag_count": len(wide_param_bags),
            "delegation_heavy_class_count": len(delegation_classes),
            "facade_module_count": len(facade_modules),
            "typed_dict_violation_count": total_typed_dict_violations,
            "dict_any_annotation_count": len(dict_any_annotations or []),
            "enum_bypass_count": len(enum_bypass_patterns or []),
        },
        "sub_axes": sub_axes,
    }

    context.update(
        _build_abstraction_leverage_context(
            util_files=util_files,
            wrappers_by_file=wrappers_by_file,
        )
    )
    context.update(
        _build_indirection_cost_context(
            indirection_hotspots=indirection_hotspots,
            wide_param_bags=wide_param_bags,
        )
    )
    context.update(
        _build_interface_honesty_context(one_impl_interfaces=one_impl_interfaces)
    )
    context.update(
        _build_delegation_density_context(delegation_classes=delegation_classes)
    )
    context.update(
        _build_definition_directness_context(facade_modules=facade_modules)
    )
    context.update(
        _build_type_discipline_context(
            typed_dict_violations=typed_dict_violations,
            dict_any_annotations=dict_any_annotations,
            enum_bypass_patterns=enum_bypass_patterns,
            type_strategy_census=type_strategy_census,
        )
    )

    return context


@dataclasses.dataclass
class _AbstractionsCollector:
    """Accumulated state for the abstractions scan pass."""

    util_files: list = dataclasses.field(default_factory=list)
    wrappers_by_file: list[dict[str, object]] = dataclasses.field(default_factory=list)
    interface_declarations: dict[str, set[str]] = dataclasses.field(
        default_factory=lambda: defaultdict(set)
    )
    implementations: dict[str, set[str]] = dataclasses.field(
        default_factory=lambda: defaultdict(set)
    )
    indirection_hotspots: list[dict[str, object]] = dataclasses.field(
        default_factory=list
    )
    wide_param_bags: list[dict[str, object]] = dataclasses.field(default_factory=list)
    delegation_classes: list[dict] = dataclasses.field(default_factory=list)
    facade_modules: list[dict] = dataclasses.field(default_factory=list)
    typed_dict_defs: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    parsed_trees: dict[str, ast.Module] = dataclasses.field(default_factory=dict)
    total_function_signatures: int = 0
    total_wrappers: int = 0


def _scan_file(
    col: _AbstractionsCollector, filepath: str, content: str
) -> None:
    """Scan a single file and accumulate results into *col*."""
    rpath = rel(filepath)
    loc = len(content.splitlines())
    basename = Path(rpath).stem.lower()
    if basename in {"utils", "helpers", "util", "helper", "common", "misc"}:
        col.util_files.append(
            {"file": rpath, "loc": loc, "excerpt": file_excerpt(filepath) or ""}
        )

    signatures = _DEF_SIGNATURE_RE.findall(content)
    col.total_function_signatures += len(signatures)

    ts_wrappers = [
        (wrapper, target)
        for wrapper, target in _TS_PASSTHROUGH_RE.findall(content)
        if wrapper != target
    ]

    for match in _INTERFACE_RE.finditer(content):
        iface = match.group(1) or match.group(2)
        if iface:
            col.interface_declarations[iface].add(rpath)

    for match in _IMPLEMENTS_RE.finditer(content):
        for iface in _extract_type_names(match.group(1)):
            col.implementations[iface].add(rpath)
    for match in _INHERITS_RE.finditer(content):
        blob = match.group(1) or match.group(2) or ""
        for iface in _extract_type_names(blob):
            col.implementations[iface].add(rpath)

    chain_matches = _CHAIN_RE.findall(content)
    max_chain_depth = max((token.count(".") for token in chain_matches), default=0)
    if max_chain_depth >= 3 or len(chain_matches) >= 6:
        col.indirection_hotspots.append(
            {
                "file": rpath,
                "max_chain_depth": max_chain_depth,
                "chain_count": len(chain_matches),
            }
        )

    wide_functions = sum(
        1 for params_blob in signatures if _count_signature_params(params_blob) >= 7
    )
    bag_mentions = len(_CONFIG_BAG_RE.findall(content))
    if wide_functions > 0 or bag_mentions >= 10:
        col.wide_param_bags.append(
            {
                "file": rpath,
                "wide_functions": wide_functions,
                "config_bag_mentions": bag_mentions,
            }
        )

    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None

    if tree is not None:
        col.parsed_trees[filepath] = tree
        py_wrappers = _find_python_passthrough_wrappers(tree)
        for entry in _find_delegation_heavy_classes(tree):
            col.delegation_classes.append({"file": rpath, **entry})
        facade_result = _find_facade_modules(tree, loc=loc)
        if facade_result is not None:
            col.facade_modules.append({"file": rpath, **facade_result})
        _collect_typed_dict_defs(tree, col.typed_dict_defs)
    else:
        py_wrappers = []

    wrapper_pairs = py_wrappers + ts_wrappers
    if wrapper_pairs:
        col.total_wrappers += len(wrapper_pairs)
        col.wrappers_by_file.append(
            {
                "file": rpath,
                "count": len(wrapper_pairs),
                "samples": [f"{w}->{t}" for w, t in wrapper_pairs[:5]],
            }
        )


def _derive_post_scan_results(col: _AbstractionsCollector) -> dict:
    """Compute derived analyses from the completed scan collector."""
    one_impl_interfaces: list[dict[str, object]] = []
    for iface, declared_in in col.interface_declarations.items():
        implemented_in = sorted(col.implementations.get(iface, set()))
        if len(implemented_in) != 1:
            continue
        one_impl_interfaces.append(
            {
                "interface": iface,
                "declared_in": sorted(declared_in),
                "implemented_in": implemented_in,
            }
        )

    typed_dict_violations = _find_typed_dict_usage_violations(
        col.parsed_trees, col.typed_dict_defs
    )[:20]
    total_typed_dict_violations = sum(
        v.get("count", 1) for v in typed_dict_violations
    )
    typed_dict_violation_files = {v["file"] for v in typed_dict_violations}

    all_td_names = set(col.typed_dict_defs.keys())
    dict_any_annotations = _find_dict_any_annotations(
        col.parsed_trees, all_td_names
    )[:30]

    enum_defs = _collect_enum_defs(col.parsed_trees)
    enum_bypass_patterns = _find_enum_bypass(col.parsed_trees, enum_defs)[:30]

    type_strategy_census = _census_type_strategies(col.parsed_trees)

    return {
        "one_impl_interfaces": one_impl_interfaces,
        "typed_dict_violations": typed_dict_violations,
        "total_typed_dict_violations": total_typed_dict_violations,
        "typed_dict_violation_files": typed_dict_violation_files,
        "dict_any_annotations": dict_any_annotations,
        "enum_bypass_patterns": enum_bypass_patterns,
        "type_strategy_census": type_strategy_census,
    }


def _sort_and_trim(col: _AbstractionsCollector, derived: dict) -> None:
    """Sort and trim collected lists in-place for final output."""
    col.wrappers_by_file.sort(key=lambda item: -int(item["count"]))
    col.indirection_hotspots.sort(
        key=lambda item: (-int(item["max_chain_depth"]), -int(item["chain_count"]))
    )
    col.wide_param_bags.sort(
        key=lambda item: (
            -int(item["wide_functions"]),
            -int(item["config_bag_mentions"]),
        )
    )
    derived["one_impl_interfaces"].sort(key=lambda item: str(item["interface"]))
    col.delegation_classes.sort(key=lambda d: -d["delegation_ratio"])
    col.delegation_classes = col.delegation_classes[:20]
    col.facade_modules.sort(key=lambda d: -d["re_export_ratio"])
    col.facade_modules = col.facade_modules[:20]


def _abstractions_context(file_contents: dict[str, str]) -> dict:
    col = _AbstractionsCollector()

    for filepath, content in file_contents.items():
        _scan_file(col, filepath, content)

    derived = _derive_post_scan_results(col)
    _sort_and_trim(col, derived)

    wrapper_rate = col.total_wrappers / max(col.total_function_signatures, 1)
    sub_axes = _compute_sub_axes(
        wrapper_rate=wrapper_rate,
        util_files=col.util_files,
        indirection_hotspots=col.indirection_hotspots,
        wide_param_bags=col.wide_param_bags,
        one_impl_interfaces=derived["one_impl_interfaces"],
        delegation_classes=col.delegation_classes,
        facade_modules=col.facade_modules,
        typed_dict_violation_files=derived["typed_dict_violation_files"],
        total_typed_dict_violations=derived["total_typed_dict_violations"],
        dict_any_count=len(derived["dict_any_annotations"]),
        enum_bypass_count=len(derived["enum_bypass_patterns"]),
    )

    return _assemble_context(
        util_files=col.util_files,
        wrapper_rate=wrapper_rate,
        total_wrappers=col.total_wrappers,
        total_function_signatures=col.total_function_signatures,
        wrappers_by_file=col.wrappers_by_file,
        one_impl_interfaces=derived["one_impl_interfaces"],
        indirection_hotspots=col.indirection_hotspots,
        wide_param_bags=col.wide_param_bags,
        delegation_classes=col.delegation_classes,
        facade_modules=col.facade_modules,
        typed_dict_violations=derived["typed_dict_violations"],
        total_typed_dict_violations=derived["total_typed_dict_violations"],
        sub_axes=sub_axes,
        dict_any_annotations=derived["dict_any_annotations"],
        enum_bypass_patterns=derived["enum_bypass_patterns"],
        type_strategy_census=derived["type_strategy_census"],
    )


__all__ = [
    "_abstractions_context",
    "_assemble_context",
    "_build_abstraction_leverage_context",
    "_build_definition_directness_context",
    "_build_delegation_density_context",
    "_build_indirection_cost_context",
    "_build_interface_honesty_context",
    "_build_type_discipline_context",
    "_compute_sub_axes",
]
