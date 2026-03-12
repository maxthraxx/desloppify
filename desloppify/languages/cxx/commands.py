"""C/C++ detect command registry."""

from __future__ import annotations

from collections.abc import Callable

from desloppify.languages._framework.commands_base import (
    make_cmd_complexity,
    make_cmd_large,
)
from desloppify.languages._framework.commands_base_registry import (
    build_standard_detect_registry,
    compose_detect_registry,
    make_cmd_cycles,
    make_cmd_deps,
    make_cmd_dupes,
    make_cmd_orphaned,
)
from desloppify.languages.cxx._helpers import build_cxx_dep_graph
from desloppify.languages.cxx.extractors import extract_all_cxx_functions, find_cxx_files
from desloppify.languages.cxx.phases import CXX_COMPLEXITY_SIGNALS

cmd_large = make_cmd_large(
    find_cxx_files,
    default_threshold=500,
    module_name=__name__,
)
cmd_complexity = make_cmd_complexity(
    find_cxx_files,
    CXX_COMPLEXITY_SIGNALS,
    default_threshold=15,
    module_name=__name__,
)
cmd_deps = make_cmd_deps(
    build_dep_graph_fn=build_cxx_dep_graph,
    empty_message="No C/C++ dependencies detected.",
    import_count_label="Includes",
    top_imports_label="Top includes",
    module_name=__name__,
)
cmd_cycles = make_cmd_cycles(build_dep_graph_fn=build_cxx_dep_graph, module_name=__name__)
cmd_orphaned = make_cmd_orphaned(
    build_dep_graph_fn=build_cxx_dep_graph,
    extensions=[".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"],
    extra_entry_patterns=["/main.c", "/main.cc", "/main.cpp", "/main.cxx"],
    extra_barrel_names=set(),
    module_name=__name__,
)
cmd_dupes = make_cmd_dupes(
    extract_functions_fn=extract_all_cxx_functions,
    module_name=__name__,
)


def get_detect_commands() -> dict[str, Callable[..., None]]:
    """Return the canonical detect command registry for C/C++."""
    return compose_detect_registry(
        base_registry=build_standard_detect_registry(
            cmd_deps=cmd_deps,
            cmd_cycles=cmd_cycles,
            cmd_orphaned=cmd_orphaned,
            cmd_dupes=cmd_dupes,
            cmd_large=cmd_large,
            cmd_complexity=cmd_complexity,
        ),
    )
