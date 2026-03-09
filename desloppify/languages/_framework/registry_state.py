"""Shared mutable registry state for language plugin discovery."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import ItemsView
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from desloppify.languages._framework.base.types import LangConfig

__all__ = [
    "RegistryContext",
    "create_registry_context",
    "current_registry_context",
    "registry_context_scope",
    "register",
    "get",
    "all_items",
    "all_keys",
    "is_registered",
    "remove",
    "clear",
    "set_load_attempted",
    "was_load_attempted",
    "record_load_error",
    "set_load_errors",
    "get_load_errors",
]

@dataclass
class RegistryContext:
    """Mutable language-registry state container."""

    registry: dict[str, LangConfig] = field(default_factory=dict)
    load_attempted: bool = False
    load_errors: dict[str, BaseException] = field(default_factory=dict)


def create_registry_context() -> RegistryContext:
    """Create an isolated language-registry context."""
    return RegistryContext()


_STATE = create_registry_context()
_REGISTRY_CONTEXT: ContextVar[RegistryContext] = ContextVar(
    "desloppify_language_registry_context",
    default=_STATE,
)


def current_registry_context() -> RegistryContext:
    """Return the active language-registry context."""
    return _REGISTRY_CONTEXT.get()


@contextmanager
def registry_context_scope(context: RegistryContext | None = None):
    """Run code with a specific language-registry context."""
    if context is None:
        yield current_registry_context()
        return
    token = _REGISTRY_CONTEXT.set(context)
    try:
        yield context
    finally:
        _REGISTRY_CONTEXT.reset(token)


def _ctx(context: RegistryContext | None = None) -> RegistryContext:
    return context if context is not None else current_registry_context()

# ── Public API ────────────────────────────────────────────


def register(name: str, cfg: LangConfig, *, context: RegistryContext | None = None) -> None:
    """Register a language config by name."""
    _ctx(context).registry[name] = cfg


def get(name: str, *, context: RegistryContext | None = None) -> LangConfig | None:
    """Get a language config by name, or None."""
    return _ctx(context).registry.get(name)


def all_items(*, context: RegistryContext | None = None) -> ItemsView[str, LangConfig]:
    """Return all (name, config) pairs."""
    return _ctx(context).registry.items()


def all_keys(*, context: RegistryContext | None = None) -> list[str]:
    """Return all registered language names."""
    return list(_ctx(context).registry.keys())


def is_registered(name: str, *, context: RegistryContext | None = None) -> bool:
    """Check if a language is registered."""
    return name in _ctx(context).registry


def remove(name: str, *, context: RegistryContext | None = None) -> None:
    """Remove a language by name (for testing)."""
    _ctx(context).registry.pop(name, None)


def clear(*, context: RegistryContext | None = None) -> None:
    """Full reset: registrations, load-attempted flag, and load errors."""
    ctx = _ctx(context)
    ctx.registry.clear()
    ctx.load_attempted = False
    ctx.load_errors.clear()


def set_load_attempted(value: bool, *, context: RegistryContext | None = None) -> None:
    """Set the load-attempted flag."""
    _ctx(context).load_attempted = value


def was_load_attempted(*, context: RegistryContext | None = None) -> bool:
    """Check whether plugin loading has been attempted."""
    return _ctx(context).load_attempted


def record_load_error(
    name: str,
    error: BaseException,
    *,
    context: RegistryContext | None = None,
) -> None:
    """Record an import error for a language module."""
    _ctx(context).load_errors[name] = error


def set_load_errors(
    errors: dict[str, BaseException],
    *,
    context: RegistryContext | None = None,
) -> None:
    """Replace the full load-errors dict (used by discovery)."""
    ctx = _ctx(context)
    ctx.load_errors.clear()
    ctx.load_errors.update(errors)


def get_load_errors(*, context: RegistryContext | None = None) -> dict[str, BaseException]:
    """Return a copy of the load-errors dict."""
    return dict(_ctx(context).load_errors)
