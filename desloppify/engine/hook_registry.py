"""Registry for optional language hook modules consumed by detectors."""

from __future__ import annotations

import importlib
import logging
import sys
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HookRegistryContext:
    """Mutable hook-registry context."""

    hooks: dict[str, dict[str, object]] = field(
        default_factory=lambda: defaultdict(dict)
    )


def create_hook_registry_context() -> HookRegistryContext:
    """Create an isolated hook-registry context."""
    return HookRegistryContext()


_STATE = create_hook_registry_context()
_HOOK_CONTEXT: ContextVar[HookRegistryContext] = ContextVar(
    "desloppify_hook_registry_context",
    default=_STATE,
)


def current_hook_registry_context() -> HookRegistryContext:
    """Return the active hook-registry context."""
    return _HOOK_CONTEXT.get()


@contextmanager
def hook_registry_scope(context: HookRegistryContext | None = None):
    """Run code with a specific hook-registry context."""
    if context is None:
        yield current_hook_registry_context()
        return
    token = _HOOK_CONTEXT.set(context)
    try:
        yield context
    finally:
        _HOOK_CONTEXT.reset(token)


def _ctx(context: HookRegistryContext | None = None) -> HookRegistryContext:
    return context if context is not None else current_hook_registry_context()


def register_lang_hooks(
    lang_name: str,
    *,
    test_coverage: object | None = None,
) -> None:
    """Register optional detector hook modules for a language."""
    hooks = _ctx().hooks[lang_name]
    if test_coverage is not None:
        hooks["test_coverage"] = test_coverage


def _bootstrap_language_module(module: object) -> None:
    """Run optional language-module bootstrap hook."""
    register_fn = getattr(module, "register", None)
    if register_fn is None:
        return
    if not callable(register_fn):
        raise TypeError("Language module register entrypoint must be callable")
    register_fn()


def _get_lang_hook(
    lang_name: str | None,
    hook_name: str,
    *,
    context: HookRegistryContext | None = None,
) -> object | None:
    if not lang_name:
        return None
    hook_state = _ctx(context)
    hook = hook_state.hooks.get(lang_name, {}).get(hook_name)
    if hook is not None:
        return hook

    module_name = f"desloppify.languages.{lang_name}"
    module = sys.modules.get(module_name)

    # Lazy-load only the requested language package.
    if module is None:
        try:
            module = importlib.import_module(module_name)
            _bootstrap_language_module(module)
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as exc:
            logger.debug(
                "Unable to import language hook package %s: %s", lang_name, exc
            )
            return None
    elif lang_name not in hook_state.hooks:
        try:
            module = importlib.reload(module)
            _bootstrap_language_module(module)
        except (ImportError, ValueError, TypeError, RuntimeError, OSError) as exc:
            logger.debug(
                "Unable to reload language hook package %s: %s", lang_name, exc
            )
            return None

    return hook_state.hooks.get(lang_name, {}).get(hook_name)


def get_lang_hook(
    lang_name: str | None,
    hook_name: str,
    *,
    context: HookRegistryContext | None = None,
) -> object | None:
    """Get a previously-registered language hook module."""
    with hook_registry_scope(context):
        return _get_lang_hook(lang_name, hook_name, context=context)


def clear_lang_hooks_for_tests(*, context: HookRegistryContext | None = None) -> None:
    """Clear registry (test helper)."""
    _ctx(context).hooks.clear()


__all__ = [
    "HookRegistryContext",
    "clear_lang_hooks_for_tests",
    "create_hook_registry_context",
    "current_hook_registry_context",
    "get_lang_hook",
    "hook_registry_scope",
    "register_lang_hooks",
]
