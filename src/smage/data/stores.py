"""Module state stores (gates.json, draft params).

Qt-free: paths and JSON only.
"""
from __future__ import annotations

import copy
import shutil

from smage.core.gates import _resolve_gates_for_file
from smage.data import project_paths as _project_paths


def _attach_gating_stores(ctx, task: dict) -> None:
    """Stash the gate tree of every Gating task in a copied subtree.

    A Gating module keeps its gate tree in ``Module/<id>/gates.json`` rather
    than in ``project.json``, so a plain deepcopy would lose it.
    """
    stack = [task]
    while stack:
        node = stack.pop()
        stack.extend(node.get("children", []))
        if ctx.project is None or not node.get("id"):
            continue
        if node.get("type") == "region":
            from smage.data import regions as _regions

            local = _regions._region_store_load(ctx, node)
            if local.get("gates") or local.get("files") or local.get("regions"):
                node["_region_store"] = copy.deepcopy(local)
            continue
        if node.get("type") != "gating":
            continue
        store = ctx._gating_store_load(node)
        if store.get("gates") or store.get("files") or store.get("per_file"):
            node["_gating_store"] = copy.deepcopy(store)


def _restore_region_store(ctx, task: dict, store: dict,
                          mapping: dict | None = None) -> None:
    """Write a pasted Region store to the new module folder."""
    from smage.data import regions as _regions

    mapping = mapping or {}
    if mapping:
        store["files"] = [
            ctx._remap_path_text(path, mapping)
            for path in store.get("files") or []
        ]
        store["per_file"] = {
            gate_id: {
                ctx._remap_path_text(path, mapping): geometry
                for path, geometry in (overrides or {}).items()
            }
            for gate_id, overrides in (store.get("per_file") or {}).items()
        }
        for region in store.get("regions") or []:
            samples = region.get("samples") or {}
            region["samples"] = {
                ctx._remap_path_text(str(path), mapping): value
                for path, value in samples.items()
            }
    # Keep the saved selection even when the pasted location cannot see the
    # files yet (the upstream modules of a copied branch are recalculated
    # later). The module shows the files as unavailable until then.
    _regions._region_store_save(ctx, task, store)


def _restore_gating_stores(ctx, task: dict, mapping: dict | None=None) -> None:
    """Write a pasted subtree's stashed gate trees to the new module ids."""
    mapping = mapping or {}
    stack = [task]
    while stack:
        node = stack.pop()
        stack.extend(node.get("children", []))
        region_store = node.pop("_region_store", None)
        if isinstance(region_store, dict):
            _restore_region_store(ctx, node, region_store, mapping)
        store = node.pop("_gating_store", None)
        if not isinstance(store, dict):
            continue
        # Gate trees reference upstream data files; follow the pasted copy.
        if mapping:
            store["files"] = [
                ctx._remap_path_text(p, mapping)
                for p in (store.get("files") or [])
            ]
            store["per_file"] = {
                ctx._remap_path_text(path, mapping): overrides
                for path, overrides in (store.get("per_file") or {}).items()
            }
        # Keep the saved selection even when the pasted location cannot see
        # the files yet (a copied branch recalculates its upstream modules
        # later). The module shows them as unavailable until then.
        ctx._gating_store_save(node, store)


def _gating_gates_for(ctx, task_def, parquet_path=None) -> list:
    """The gate list of a Gating or Region task, resolved for `parquet_path`.

    Downstream consumers (Figure, Gate2Col, Heatmap) use this instead of
    reading ``params.gates``, so per-file adjustments are honoured and the
    gate tree no longer needs to live inside ``project.json``.  A Region
    module exposes each of its Regions as one spatial gate.
    """
    if (task_def or {}).get("type") == "region":
        # Regions are exposed downstream as converted 0/1 channels, never as
        # gates or filters.  Their internal detection gates stay private.
        return []
    if (task_def.get("params") or {}).get("gates") and not ctx._gating_store_path(task_def):
        # A legacy task without a store yet — fall back to inline gates.
        return list(task_def.get("params", {}).get("gates") or [])
    store = ctx._gating_store_load(task_def)
    gates = store.get("gates") or []
    if parquet_path is not None:
        key = str(parquet_path)
        # Gates only apply to the files selected in the module.
        if key not in set(store.get("files") or []):
            return []
        return _resolve_gates_for_file(
            gates, store.get("per_file") or {}, key
        )
    return list(gates)


def _write_draft_params(ctx, page) -> None:
    """Keep a draft module's parameters in its temporary folder."""
    task_id = page.property("task_id")
    if not task_id or str(task_id) not in ctx._draft_task_ids:
        return
    task_def = getattr(page, "_draft_task_def", None)
    folder = ctx._module_dir(task_id)
    if not isinstance(task_def, dict) or folder is None:
        return
    try:
        _project_paths._save_json(
            folder / "params.json",
            task_def.get("params") or {},
            ctx.project.path,
            indent=2,
            default=str,
        )
    except (OSError, TypeError):
        pass


def _discard_draft_folders(ctx) -> None:
    """Delete every temporary draft folder (module closed / project left)."""
    ids = list(getattr(ctx, "_draft_task_ids", ()))
    ctx._draft_task_ids = set()
    # Session unlocks never outlive the project session.
    ctx._session_unlocked = set()
    if ctx.project is None:
        return
    for task_id in ids:
        shutil.rmtree(
            ctx.project.path / "Module" / str(task_id) / "draft",
            ignore_errors=True,
        )
