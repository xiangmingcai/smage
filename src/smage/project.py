"""Project management for Smage.

A Smage project is a folder named ``<name>.smage`` containing:

- ``project.json`` — a small manifest holding the project metadata, the sample
  list, and the workflow (the task tree with every module's parameters).
- ``data/`` — normalized tabular data files (one Parquet per imported sample).
- ``Module/`` — per-module persisted data (each module gets a folder named by
  its task id, e.g. ``Module/segmentation-abc123/``).
- ``tmp/`` — disposable working files (for example a segmentation batch); it
  is never referenced by ``project.json`` and may be deleted at any time.

Only the "Data Import" module persists tabular data. The Segmentation and
Illumination modules persist derived artifacts (masks) under ``Module/``; every
other module computes its results on demand.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from smage.data import project_paths as _project_paths

PROJECT_VERSION = "0.5.3"
DATA_DIRNAME = "data"
MANIFEST_FILENAME = "project.json"
# Window/UI preferences (tree expansion, …), kept out of the manifest so they
# never mix with the data description.
UI_STATE_FILENAME = "ui_state.json"
# Each module's own persisted files live under this folder, keyed by task id.
MODULE_DIRNAME = "Module"
# Disposable working files (large segmentation batches, previews, …). Safe to
# delete at any time; never referenced by project.json.
TMP_DIRNAME = "tmp"


class Project:
    """A Smage project: a folder with a manifest and a data store."""

    def __init__(self, path: Path, name: str, created: str | None = None,
                 identity_root=None) -> None:
        # Keep one canonical form for the live path.  On Windows a copied
        # project may be opened through an 8.3 short name; resolving it makes
        # decoded project:// paths and in-memory keys agree.
        self.path = Path(path).resolve()
        self.name = name
        self.data_dir = self.path / DATA_DIRNAME
        self.created = created or datetime.now(timezone.utc).isoformat()
        # Stable prefix for hashes and sampling.  It is deliberately not used
        # for file access, so a copied project keeps its saved results.
        self.identity_root = str(identity_root or self.path)
        self.path_format = _project_paths.PATH_FORMAT
        self.samples: list = []
        # Every new project starts with the single fixed "Data Import" task.
        self.workflow: dict = {
            "tasks": [
                {
                    "id": "data-import",
                    "type": "import",
                    "name": "Data Import",
                    "params": {},
                    "children": [],
                }
            ]
        }

    @classmethod
    def create(cls, parent_dir: Path, name: str) -> "Project":
        """Create a new project folder and return the Project."""
        folder = parent_dir / f"{name}.smage"
        folder.mkdir(parents=True)
        (folder / DATA_DIRNAME).mkdir()
        project = cls(folder, name, identity_root=folder.resolve())
        project.save()
        return project

    @classmethod
    def load(cls, path: Path) -> "Project":
        """Load an existing project folder from its ``project.json``.

        Older projects stored absolute paths.  They are migrated once to
        ``project://...`` paths, and their old root becomes the stable
        ``identity_root`` so saved output signatures and Subsample choices do
        not change.
        """
        path = Path(path)
        manifest_path = path / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise ValueError(f"No {MANIFEST_FILENAME} found in {path}")
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)

        old_format = int(manifest.get("path_format") or 0)
        identity = manifest.get("identity_root")
        legacy = (
            old_format < _project_paths.PATH_FORMAT
            or not identity
            or _project_paths._has_absolute_project_paths(
                manifest, Path(path).resolve()
            )
        )
        inferred = None
        if legacy:
            inferred = _project_paths._infer_project_root(
                manifest, path / MODULE_DIRNAME
            )
        # A copied legacy project still names its old root in every path.
        # Inferring from those paths keeps its existing hashes valid.
        if not identity:
            identity = str(inferred or Path(path).resolve())

        project = cls(
            path, manifest["name"], manifest.get("created"),
            identity_root=identity,
        )
        old_root = inferred
        if old_root is None and legacy and identity:
            old_root = Path(identity)
        if old_root is None:
            old_root = project.path
        project.samples = _project_paths._resolve_paths(
            manifest.get("samples", []), old_root, project.path
        )
        project.workflow = _project_paths._resolve_paths(
            manifest.get("workflow", {"tasks": []}), old_root, project.path
        )
        project._hydrate_masks(project.workflow)

        # Legacy Module stores contain absolute paths too.  Rewrite them even
        # when the manifest had no data records of its own.
        needs_store_migration = (
            legacy
            or os.path.normcase(str(old_root))
            != os.path.normcase(str(project.path))
        )
        stores_changed = False
        if needs_store_migration:
            stores_changed = _project_paths._migrate_module_stores(
                project.path, old_root
            )
        if needs_store_migration or stores_changed or not manifest.get("identity_root"):
            project.save()
        return project

    def save(self) -> None:
        """Write the project manifest to ``project.json``.

        Paths inside the project are stored as ``project://...`` so the whole
        folder can be copied or moved.  Illumination masks are written to
        compact float32 ``.npy`` files and only a relative ``mask_file`` path
        is kept in the manifest, so the JSON stays small even for large
        workflows. The manifest is written compactly.
        """
        manifest = {
            "name": self.name,
            "version": PROJECT_VERSION,
            "path_format": _project_paths.PATH_FORMAT,
            "identity_root": str(self.identity_root or self.path),
            "created": self.created,
            "samples": _project_paths._encode_paths(self.samples, self.path),
            "workflow": _project_paths._encode_paths(
                self.workflow, self.path
            ),
        }
        serializable = self._externalize_masks(manifest)
        (self.path / MANIFEST_FILENAME).write_text(
            json.dumps(serializable, separators=(",", ":")), encoding="utf-8"
        )

    def move_to(self, new_path: Path, new_name: str | None = None) -> None:
        """Rename/move the project folder and update the project fields.

        Project stores contain only project-relative paths, so the folder
        move itself needs no path rewriting.  ``identity_root`` deliberately
        stays unchanged.
        """
        old_path = self.path
        new_path = Path(new_path).resolve()
        if os.path.normcase(str(old_path)) == os.path.normcase(str(new_path)):
            # A case-only rename needs a temporary name on Windows.
            old_name = self.name
            moved = False
            if str(old_path) != str(new_path):
                temp = old_path.with_name(old_path.name + ".rename-tmp")
                old_path.rename(temp)
                try:
                    temp.rename(new_path)
                except Exception:
                    try:
                        temp.rename(old_path)
                    except OSError:
                        pass
                    raise
                moved = True
                self.path = new_path
                self.data_dir = new_path / DATA_DIRNAME
            try:
                self.samples = _project_paths._resolve_paths(
                    self.samples, old_path, new_path
                )
                self.workflow = _project_paths._resolve_paths(
                    self.workflow, old_path, new_path
                )
                _project_paths._migrate_module_stores(new_path, old_path)
                if new_name:
                    self.name = new_name
                self.save()
            except Exception:
                self.samples = _project_paths._resolve_paths(
                    self.samples, new_path, old_path
                )
                self.workflow = _project_paths._resolve_paths(
                    self.workflow, new_path, old_path
                )
                self.name = old_name
                self.path = old_path
                self.data_dir = old_path / DATA_DIRNAME
                if moved:
                    try:
                        new_path.rename(old_path)
                    except OSError:
                        pass
                raise
            return
        old_path.rename(new_path)
        old_name = self.name
        try:
            self.path = new_path
            self.data_dir = new_path / DATA_DIRNAME
            self.samples = _project_paths._resolve_paths(
                self.samples, old_path, new_path
            )
            self.workflow = _project_paths._resolve_paths(
                self.workflow, old_path, new_path
            )
            _project_paths._migrate_module_stores(new_path, old_path)
            if new_name:
                self.name = new_name
            self.save()
        except Exception:
            self.samples = _project_paths._resolve_paths(
                self.samples, new_path, old_path
            )
            self.workflow = _project_paths._resolve_paths(
                self.workflow, new_path, old_path
            )
            self.name = old_name
            self.path = old_path
            self.data_dir = old_path / DATA_DIRNAME
            try:
                new_path.rename(old_path)
            except OSError:
                pass
            raise

    def _mask_path(self, task_id: str, sub_id: str) -> Path:
        """Absolute path of an illumination submodule's mask file."""
        return self.path / MODULE_DIRNAME / str(task_id) / f"{sub_id}.npy"

    def _externalize_masks(self, node, task_id=None):
        """Return a JSON-safe copy of `node` with illumination masks externalized.

        An illumination submodule carries a 2-D ``mask`` (a list of lists, or a
        numpy array). It is written to a float32 ``.npy`` file under its
        module's own ``Module/<task-id>/`` folder and replaced by a relative
        ``mask_file`` path so it never lands in the JSON. The input `node` is
        not modified.
        """
        if isinstance(node, dict):
            # A workflow task; remember it so masks go under its folder.
            if "type" in node and "id" in node:
                task_id = node.get("id")
            mask = node.get("mask")
            if mask is not None and (
                isinstance(mask, (list, tuple)) or hasattr(mask, "dtype")
            ):
                sub_id = node.get("id")
                if sub_id is not None and task_id:
                    rel = f"{MODULE_DIRNAME}/{task_id}/{sub_id}.npy"
                    path = self._mask_path(str(task_id), str(sub_id))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    np.save(path, np.asarray(mask, dtype="float32"))
                    out = {}
                    for key, value in node.items():
                        if key != "mask":
                            out[key] = self._externalize_masks(value, task_id)
                    out["mask_file"] = rel
                    return out
            return {k: self._externalize_masks(v, task_id) for k, v in node.items()}
        if isinstance(node, list):
            return [self._externalize_masks(v, task_id) for v in node]
        return node

    def _hydrate_masks(self, node) -> None:
        """Load ``mask_file`` entries back into in-memory ``mask`` lists."""
        if isinstance(node, dict):
            mask_file = node.get("mask_file")
            if mask_file:
                path = self.path / mask_file
                mask = None
                if path.is_file():
                    try:
                        mask = np.load(path).tolist()
                    except (OSError, ValueError):
                        mask = None
                node["mask"] = mask
                node.pop("mask_file", None)
            for value in node.values():
                self._hydrate_masks(value)
        elif isinstance(node, list):
            for value in node:
                self._hydrate_masks(value)

    @property
    def tmp_dir(self) -> Path:
        """Disposable working files for long-running modules."""
        return self.path / TMP_DIRNAME

    @property
    def columns_path(self) -> Path:
        """Path to the lightweight column-name index for the data store."""
        return self.data_dir / "columns.json"

    def load_columns(self) -> dict:
        """Return the column-name index ({relative_path: [columns]}), or {}."""
        if not self.columns_path.is_file():
            return {}
        try:
            with self.columns_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    def save_columns(self, columns: dict) -> None:
        """Write the column-name index."""
        self.columns_path.parent.mkdir(parents=True, exist_ok=True)
        self.columns_path.write_text(json.dumps(columns, indent=2), encoding="utf-8")

    @property
    def ui_state_path(self) -> Path:
        """Path to the small UI-state file (tree expansion, window layout)."""
        return self.path / UI_STATE_FILENAME

    def load_ui_state(self) -> dict:
        """Return the saved UI state, or {} when there is none."""
        if not self.ui_state_path.is_file():
            return {}
        try:
            with self.ui_state_path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, ValueError):
            return {}
        if not isinstance(state, dict):
            return {}
        return _project_paths._decode_paths(state, self.path)

    def save_ui_state(self, state: dict) -> None:
        """Write the UI state (best effort; it is only a convenience)."""
        try:
            self.ui_state_path.write_text(
                json.dumps(
                    _project_paths._encode_paths(state, self.path), indent=2
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
