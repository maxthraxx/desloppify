"""Canonical detector registry — single source of truth.

All detector metadata lives here. Other modules derive their views
(display order, CLI names, narrative tools, scoring validation) from this registry
instead of maintaining their own lists.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, MutableMapping, Set
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from desloppify.base.registry_catalog_entries import DETECTORS as _CATALOG_DETECTORS
from desloppify.base.registry_catalog_models import DISPLAY_ORDER, DetectorMeta

_BASE_DETECTORS: dict[str, DetectorMeta] = dict(_CATALOG_DETECTORS)
_BASE_DISPLAY_ORDER: list[str] = list(DISPLAY_ORDER)
_BASE_JUDGMENT_DETECTORS: frozenset[str] = frozenset(
    name for name, meta in _BASE_DETECTORS.items() if meta.needs_judgment
)


@dataclass
class DetectorRegistryContext:
    """Mutable detector-registry context with explicit lifecycle."""

    detectors: dict[str, DetectorMeta]
    display_order: list[str]
    callbacks: list[Callable[[], None]] = field(default_factory=list)
    judgment_detectors: frozenset[str] = field(default_factory=frozenset)


def create_detector_registry_context(
    *,
    callbacks: list[Callable[[], None]] | None = None,
) -> DetectorRegistryContext:
    """Create an isolated detector-registry context seeded from built-ins."""
    detectors = dict(_CATALOG_DETECTORS)
    return DetectorRegistryContext(
        detectors=detectors,
        display_order=list(DISPLAY_ORDER),
        callbacks=list(callbacks or []),
        judgment_detectors=frozenset(
            name for name, meta in detectors.items() if meta.needs_judgment
        ),
    )


_RUNTIME = create_detector_registry_context()
_REGISTRY_CONTEXT: ContextVar[DetectorRegistryContext] = ContextVar(
    "desloppify_detector_registry_context",
    default=_RUNTIME,
)


def current_detector_registry_context() -> DetectorRegistryContext:
    """Return the active detector-registry context."""
    return _REGISTRY_CONTEXT.get()


@contextmanager
def detector_registry_scope(context: DetectorRegistryContext | None = None):
    """Run code with an explicit detector-registry context."""
    if context is None:
        yield current_detector_registry_context()
        return
    token = _REGISTRY_CONTEXT.set(context)
    try:
        yield context
    finally:
        _REGISTRY_CONTEXT.reset(token)


def _ctx(context: DetectorRegistryContext | None = None) -> DetectorRegistryContext:
    return context if context is not None else current_detector_registry_context()


class _DetectorMap(MutableMapping[str, DetectorMeta]):
    """Live mapping view over the active detector registry."""

    def _mapping(self) -> dict[str, DetectorMeta]:
        return _ctx().detectors

    def __getitem__(self, key: str) -> DetectorMeta:
        return self._mapping()[key]

    def __setitem__(self, key: str, value: DetectorMeta) -> None:
        self._mapping()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping())

    def __len__(self) -> int:
        return len(self._mapping())

    def get(self, key: str, default: DetectorMeta | None = None) -> DetectorMeta | None:
        return self._mapping().get(key, default)

    def items(self):  # pragma: no cover - delegated mapping view
        return self._mapping().items()


class _DisplayOrderView:
    """Live sequence-like view over the active display order."""

    def _values(self) -> list[str]:
        return _ctx().display_order

    def __iter__(self) -> Iterator[str]:
        return iter(self._values())

    def __len__(self) -> int:
        return len(self._values())

    def __contains__(self, item: object) -> bool:
        return item in self._values()

    def remove(self, value: str) -> None:
        self._values().remove(value)

    def append(self, value: str) -> None:
        self._values().append(value)

    def extend(self, values: list[str]) -> None:
        self._values().extend(values)

    def clear(self) -> None:
        self._values().clear()


class _CallbackView:
    """Live list-like view over detector-registration callbacks."""

    def _values(self) -> list[Callable[[], None]]:
        return _ctx().callbacks

    def append(self, callback: Callable[[], None]) -> None:
        self._values().append(callback)

    def __iter__(self) -> Iterator[Callable[[], None]]:
        return iter(self._values())

    def __len__(self) -> int:
        return len(self._values())


# Module-level handles into the active runtime registry.
DETECTORS: MutableMapping[str, DetectorMeta] = _DetectorMap()
_DISPLAY_ORDER = _DisplayOrderView()
_on_register_callbacks = _CallbackView()


class _JudgmentDetectorsView(Set[str]):
    """Live read-only set view over runtime judgment detector names."""

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item in _ctx().judgment_detectors

    def __iter__(self) -> Iterator[str]:
        return iter(_ctx().judgment_detectors)

    def __len__(self) -> int:
        return len(_ctx().judgment_detectors)


JUDGMENT_DETECTORS: Set[str] = _JudgmentDetectorsView()


def on_detector_registered(
    callback: Callable[[], None],
    *,
    context: DetectorRegistryContext | None = None,
) -> None:
    """Register a callback invoked after register_detector(). No-arg."""
    _ctx(context).callbacks.append(callback)


def register_detector(
    meta: DetectorMeta,
    *,
    context: DetectorRegistryContext | None = None,
) -> None:
    """Register a detector at runtime (used by generic plugins)."""
    runtime = _ctx(context)
    runtime.detectors[meta.name] = meta
    if meta.name not in runtime.display_order:
        runtime.display_order.append(meta.name)
    runtime.judgment_detectors = frozenset(
        name for name, current_meta in runtime.detectors.items()
        if current_meta.needs_judgment
    )
    for callback in tuple(runtime.callbacks):
        callback()


def reset_registered_detectors(*, context: DetectorRegistryContext | None = None) -> None:
    """Reset runtime-added detector registrations to built-in defaults."""
    runtime = _ctx(context)
    runtime.detectors.clear()
    runtime.detectors.update(_BASE_DETECTORS)
    runtime.display_order.clear()
    runtime.display_order.extend(_BASE_DISPLAY_ORDER)
    runtime.judgment_detectors = _BASE_JUDGMENT_DETECTORS
    for callback in tuple(runtime.callbacks):
        callback()


def detector_names(*, context: DetectorRegistryContext | None = None) -> list[str]:
    """All registered detector names, sorted."""
    return sorted(_ctx(context).detectors.keys())


def display_order(*, context: DetectorRegistryContext | None = None) -> list[str]:
    """Canonical display order for terminal output."""
    return list(_ctx(context).display_order)


_ACTION_PRIORITY = {"auto_fix": 0, "reorganize": 1, "refactor": 2, "manual_fix": 3}
_ACTION_LABELS = {
    "auto_fix": "autofix",
    "reorganize": "move",
    "refactor": "refactor",
    "manual_fix": "manual",
}


def dimension_action_type(
    dim_name: str,
    *,
    context: DetectorRegistryContext | None = None,
) -> str:
    """Return a compact action type label for a dimension based on its detectors."""
    best = "manual"
    best_priority = 99
    runtime = _ctx(context)
    for detector_meta in runtime.detectors.values():
        if detector_meta.dimension == dim_name:
            priority = _ACTION_PRIORITY.get(detector_meta.action_type, 99)
            if priority < best_priority:
                best_priority = priority
                best = detector_meta.action_type
    return _ACTION_LABELS.get(best, "manual")


def detector_tools(*, context: DetectorRegistryContext | None = None) -> dict[str, dict[str, Any]]:
    """Build detector tool metadata keyed by detector name."""
    result = {}
    runtime = _ctx(context)
    for detector_name, detector_meta in runtime.detectors.items():
        entry: dict[str, Any] = {
            "fixers": list(detector_meta.fixers),
            "action_type": detector_meta.action_type,
        }
        if detector_meta.tool:
            entry["tool"] = detector_meta.tool
        if detector_meta.guidance:
            entry["guidance"] = detector_meta.guidance
        result[detector_name] = entry
    return result


__all__ = [
    "DETECTORS",
    "DISPLAY_ORDER",
    "DetectorMeta",
    "DetectorRegistryContext",
    "JUDGMENT_DETECTORS",
    "_DISPLAY_ORDER",
    "_on_register_callbacks",
    "create_detector_registry_context",
    "current_detector_registry_context",
    "detector_names",
    "detector_registry_scope",
    "detector_tools",
    "dimension_action_type",
    "display_order",
    "on_detector_registered",
    "register_detector",
    "reset_registered_detectors",
]
