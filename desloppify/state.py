"""State compatibility facade.

Prefer narrower surfaces for new code:
- ``desloppify.state_io`` for persistence/schema contracts
- ``desloppify.state_scoring`` for scoring-only reads

Compatibility owner: core-platform
Removal target: 2026-06-30
"""

from desloppify.engine._state.filtering import (
    add_ignore,
    is_ignored,
    issue_in_scan_scope,
    make_issue,
    open_scope_breakdown,
    path_scoped_issues,
    remove_ignored_issues,
)
from desloppify.engine._state.merge import (
    MergeScanOptions,
    find_suspect_detectors,
    merge_scan,
    upsert_issues,
)
from desloppify.engine._state.noise import (
    DEFAULT_ISSUE_NOISE_BUDGET,
    DEFAULT_ISSUE_NOISE_GLOBAL_BUDGET,
    apply_issue_noise_budget,
    resolve_issue_noise_budget,
    resolve_issue_noise_global_budget,
    resolve_issue_noise_settings,
)
from desloppify.engine._state.persistence import load_state, save_state, state_lock
from desloppify.engine._state.resolution import (
    coerce_assessment_score,
    match_issues,
    resolve_issues,
)
from desloppify.engine._state.schema import (
    CURRENT_VERSION,
    ConcernDismissal,
    DimensionScore,
    Issue,
    ScanMetadataModel,
    StateModel,
    StateStats,
    SubjectiveAssessment,
    SubjectiveIntegrity,
    empty_state,
    ensure_state_defaults,
    get_state_dir,
    get_state_file,
    json_default,
    migrate_state_keys,
    scan_inventory_available,
    scan_metadata,
    scan_reconstructed_issue_count,
    scan_source,
    scan_metrics_available,
    utc_now,
    validate_state_invariants,
)
from desloppify.engine._state.schema_scores import (
    get_objective_score,
    get_overall_score,
    get_strict_score,
    get_verified_strict_score,
)
from desloppify.state_scoring import ScoreSnapshot, score_snapshot, suppression_metrics


__all__ = [
    # Types
    "ConcernDismissal",
    "DimensionScore",
    "Issue",
    "ScanMetadataModel",
    "MergeScanOptions",
    "ScoreSnapshot",
    "StateModel",
    "StateStats",
    "SubjectiveAssessment",
    "SubjectiveIntegrity",
    # Constants
    "CURRENT_VERSION",
    "DEFAULT_ISSUE_NOISE_BUDGET",
    "DEFAULT_ISSUE_NOISE_GLOBAL_BUDGET",
    "get_state_dir",
    "get_state_file",
    # Functions
    "apply_issue_noise_budget",
    "empty_state",
    "ensure_state_defaults",
    "json_default",
    "load_state",
    "resolve_issue_noise_budget",
    "resolve_issue_noise_global_budget",
    "resolve_issue_noise_settings",
    "save_state",
    "scan_inventory_available",
    "scan_metadata",
    "scan_reconstructed_issue_count",
    "scan_source",
    "scan_metrics_available",
    "state_lock",
    "score_snapshot",
    "suppression_metrics",
    "utc_now",
    "validate_state_invariants",
    "migrate_state_keys",
]
