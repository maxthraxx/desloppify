"""Direct tests for scoped detector registry contexts."""

from desloppify.base.registry import (
    DETECTORS,
    create_detector_registry_context,
    detector_registry_scope,
    register_detector,
)
from desloppify.base.registry_catalog_models import DetectorMeta


def test_detector_registry_scope_isolated_from_default_registry():
    scoped = create_detector_registry_context()
    scoped_meta = DetectorMeta(
        name="scoped_detector",
        display="Scoped Detector",
        dimension="maintainability",
        action_type="manual_fix",
        guidance="",
    )

    with detector_registry_scope(scoped):
        register_detector(scoped_meta)
        assert "scoped_detector" in DETECTORS

    assert "scoped_detector" not in DETECTORS
