"""Portable project paths.

Paths that point inside a ``.smage`` project are stored as
``project://data/example.parquet`` instead of an absolute path.  The running
application still works with absolute paths; encoding and decoding happen at
the project-store boundary (``project.json`` and the JSON stores under
``Module/``).  External paths, such as an imported file's original ``source``,
stay absolute.

A project also keeps an ``identity_root``.  It is deliberately *not* a
working path: hashes that must not change when the project folder moves use it
as a stable prefix.  This keeps saved module outputs and Subsample choices
valid after a copy, move or rename.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path, PurePosixPath

PROJECT_URI = "project://"
PATH_FORMAT = 2

# Project folders always end with this suffix.  It lets legacy-root inference
# ignore unrelated absolute paths (for example a raw data file that happens
# to contain a "data" directory).
PROJECT_SUFFIX = ".smage"

_MARKERS = (
    "\\data\\", "\\module\\", "\\tmp\\", "\\log\\",
    "/data/", "/module/", "/tmp/", "/log/",
)


def _root_tail(text: str, root) -> str | None:
    """Return the part of ``text`` after ``root``, or None when unrelated.

    The returned tail preserves its original separator and casing.  It is
    what makes ``_stable_path_key`` reproduce a legacy absolute path exactly.
    """
    raw = str(text)
    root_text = str(root)
    candidates = {
        root_text,
        root_text.replace("\\", "/"),
        root_text.replace("/", "\\"),
    }
    for candidate in candidates:
        base = candidate.rstrip("\\/")
        if not base or len(raw) < len(base):
            continue
        if raw[: len(base)].lower() != base.lower():
            continue
        rest = raw[len(base):]
        if rest == "" or rest[0] in "\\/":
            return rest
    return None


def _is_uri(text: str) -> bool:
    return str(text).startswith(PROJECT_URI)


def _decode_text(text: str, root) -> str:
    """Resolve one ``project://`` path against ``root``."""
    if not _is_uri(text):
        return text
    rel = str(text)[len(PROJECT_URI):].replace("\\", "/").lstrip("/")
    if not rel:
        return str(root)
    candidate = (Path(root) / PurePosixPath(rel)).resolve()
    root_resolved = Path(root).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        # A hand-edited store must not be able to escape the project.
        return text
    return str(candidate)


def _encode_text(text: str, root) -> str:
    """Encode one absolute project path as ``project://``."""
    if _is_uri(text):
        return text
    tail = _root_tail(text, root)
    if tail is None:
        return text
    rel = tail.lstrip("\\/").replace("\\", "/")
    return PROJECT_URI + rel


def _resolve_text(text: str, old_root, new_root) -> str:
    """Move one path from the previous project root to the current one."""
    if _is_uri(text):
        return _decode_text(text, new_root)
    if old_root is None:
        return text
    tail = _root_tail(text, old_root)
    if tail is None:
        return text
    return str(new_root) + tail


def _map_value(value, func):
    """Apply ``func`` to every string, including dictionary keys."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            new_key = func(key) if isinstance(key, str) else key
            out[new_key] = _map_value(item, func)
        return out
    if isinstance(value, list):
        return [_map_value(item, func) for item in value]
    if isinstance(value, tuple):
        return tuple(_map_value(item, func) for item in value)
    if isinstance(value, str):
        return func(value)
    return value


def _encode_paths(value, root):
    return _map_value(value, lambda text: _encode_text(text, root))


def _decode_paths(value, root):
    return _map_value(value, lambda text: _decode_text(text, root))


def _resolve_paths(value, old_root, new_root):
    return _map_value(
        value, lambda text: _resolve_text(text, old_root, new_root)
    )


def _iter_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, str):
        yield value


def _root_candidate(text: str) -> str | None:
    """Return a probable legacy project root from one absolute path."""
    raw = str(text)
    lower = raw.lower()
    for marker in _MARKERS:
        start = 0
        while True:
            index = lower.find(marker, start)
            if index <= 0:
                break
            root = raw[:index]
            if root.lower().endswith(PROJECT_SUFFIX):
                return root
            start = index + 1
    return None


def _has_absolute_project_paths(manifest, project_root) -> bool:
    """Whether a manifest still contains an absolute project path."""
    identity = manifest.get("identity_root") if isinstance(manifest, dict) else None
    for text in _iter_strings(manifest):
        if identity and text == identity:
            continue
        if _is_uri(text):
            continue
        if _root_tail(text, project_root) is not None:
            return True
        if identity and _root_tail(text, identity) is not None:
            return True
    return False


def _infer_project_root(manifest, module_root=None) -> Path | None:
    """Infer the root of a project saved by an older Smage version.

    Old manifests contain absolute paths such as
    ``E:\\data\\Run.smage\\data\\abc.parquet``.  The common prefix ending in
    ``.smage`` is the old root.  Checking the ``Module`` stores as well covers
    projects whose manifest itself only contains relative paths.
    """
    candidates = Counter()
    for text in _iter_strings(manifest):
        candidate = _root_candidate(text)
        if candidate:
            candidates[candidate] += 1

    if module_root is not None and Path(module_root).is_dir():
        for json_path in Path(module_root).rglob("*.json"):
            try:
                with json_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError, TypeError):
                continue
            for text in _iter_strings(data):
                candidate = _root_candidate(text)
                if candidate:
                    candidates[candidate] += 1

    if not candidates:
        return None
    # The most frequent root wins; longest breaks ties deterministically.
    root, _count = max(
        candidates.items(),
        key=lambda item: (item[1], len(item[0])),
    )
    return Path(root)


def _load_json(path, root):
    """Read a project JSON store and resolve its relative paths."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return _decode_paths(data, root)


def _save_json(path, payload, root, indent=None, default=None) -> None:
    """Write a project JSON store with project-relative paths."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _encode_paths(payload, root)
    text = json.dumps(
        encoded, indent=indent, ensure_ascii=False, default=default
    )
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _migrate_json_file(path, old_root, new_root) -> bool:
    """Resolve legacy absolute paths in one JSON file and encode them."""
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            original = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False
    resolved = _resolve_paths(original, old_root, new_root)
    encoded = _encode_paths(resolved, new_root)
    if encoded == original:
        return False
    try:
        _save_json(path, resolved, new_root)
    except OSError:
        return False
    return True


def _migrate_module_stores(root, old_root) -> bool:
    """Migrate every JSON store under ``Module/`` to portable paths."""
    root = Path(root)
    changed = False
    module_root = root / "Module"
    if module_root.is_dir():
        for json_path in module_root.rglob("*.json"):
            if _migrate_json_file(json_path, old_root, root):
                changed = True
    ui_state = root / "ui_state.json"
    if ui_state.is_file() and _migrate_json_file(ui_state, old_root, root):
        changed = True
    return changed


def _stable_path_key(ctx, path) -> str:
    """A path string that stays unchanged when the project folder moves.

    The first version of Smage hashed absolute paths.  For an old project,
    ``identity_root`` is the inferred old root, so this function reproduces
    the exact legacy string and existing signatures remain valid.  New
    projects start with ``identity_root`` equal to their current root.
    """
    project = getattr(ctx, "project", None)
    if project is None:
        return str(path)
    raw = str(path)
    if _is_uri(raw):
        return raw
    current = str(project.path)
    identity = str(
        getattr(project, "identity_root", "") or current
    )
    tail = _root_tail(raw, current)
    if tail is not None:
        # Keep the separator style of the stable prefix.  A legacy project
        # saved with forward slashes must reproduce those exact strings.
        if "\\" in identity or "/" not in identity:
            tail = tail.replace("/", "\\")
        elif "/" in identity:
            tail = tail.replace("\\", "/")
        return identity + tail
    # Already expressed against the stable root (e.g. a legacy path that was
    # not decoded for some reason): leave it untouched.
    if _root_tail(raw, identity) is not None:
        return raw
    return raw


def _stable_value(ctx, value):
    """Recursively replace project-internal paths with stable path keys."""
    return _map_value(value, lambda text: _stable_path_key(ctx, text))
