"""Application and session context shared by the data layer.

``AppContext`` is what a module receives; ``Session`` is the per-project part
(project, column index, frame caches, pool state). Both are deliberately
Qt-free.

For now they are transparent proxies over the host window: the caches still
live on ``MainWindow``, but the data modules address them through these
objects, so ownership can move here later without touching call sites.
"""
from __future__ import annotations


class Session:
    """Per-project runtime state (project, caches, pool, module registry)."""

    def __init__(self, host) -> None:
        object.__setattr__(self, "_host", host)

    def __getattr__(self, name):
        return getattr(self._host, name)

    def __setattr__(self, name, value) -> None:
        setattr(self._host, name, value)


class AppContext:
    """Everything a module needs: the session, settings and extensions."""

    def __init__(self, host) -> None:
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "session", Session(host))

    def __getattr__(self, name):
        # Cross-layer helpers that would otherwise form an import cycle are
        # imported lazily; see ``smage.data.registry``.
        from smage.data.registry import resolve

        bound = resolve(self, name)
        if bound is not None:
            object.__setattr__(self, name, bound)
            return bound
        return getattr(self.session, name)

    def __setattr__(self, name, value) -> None:
        setattr(self.session, name, value)
