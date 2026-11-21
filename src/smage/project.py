"""Project management for Smage.

A Smage project is a folder named ``<name>.smage`` containing:

- ``project.json`` — a small manifest holding the project metadata, the sample
  list, and the workflow (the task tree with every module's parameters).
- ``data/`` — normalized tabular data files (one Parquet per imported sample).
- ``Module/`` — per-module persisted data (each module gets a folder named by
  its task id, e.g. ``Module/segmentation-abc123/``).

Only the "Data Import" module persists tabular data. The Segmentation and
Illumination modules persist derived artifacts (masks) under ``Module/``; every
other module computes its results on demand.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_VERSION = "0.3.3"
DATA_DIRNAME = "data"
MANIFEST_FILENAME = "project.json"
# Window/UI preferences (tree expansion, …), kept out of the manifest so they
# never mix with the data description.
UI_STATE_FILENAME = "ui_state.json"
# Each module's own persisted files live under this folder, keyed by task id.
MODULE_DIRNAME = "Module"


class Project:
    """A Smage project: a folder with a manifest and a data store."""

    def __init__(self, path: Path, name: str, created: str | None = None) -> None:
        self.path = path
        self.name = name
        self.data_dir = path / DATA_DIRNAME
        self.created = created or datetime.now(timezone.utc).isoformat()
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
        project = cls(folder, name)
        project.save()
        return project

    @classmethod
    def load(cls, path: Path) -> "Project":
        """Load an existing project folder from its ``project.json``."""
        manifest_path = path / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise ValueError(f"No {MANIFEST_FILENAME} found in {path}")
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
        project = cls(path, manifest["name"], manifest.get("created"))
        project.samples = manifest.get("samples", [])
        project.workflow = manifest.get("workflow", {"tasks": []})
        project._hydrate_masks(project.workflow)
        return project

    def save(self) -> None:
        """Write the project manifest to ``project.json``.

        Illumination masks are written to compact float32 ``.npy`` files and
        only a relative ``mask_file`` path is kept in the manifest, so the JSON
        stays small even for large workflows. The manifest is written compactly.
        """
        manifest = {
            "name": self.name,
            "version": PROJECT_VERSION,
            "created": self.created,
            "samples": self.samples,
            "workflow": self.workflow,
        }
        serializable = self._externalize_masks(manifest)
        (self.path / MANIFEST_FILENAME).write_text(
            json.dumps(serializable, separators=(",", ":")), encoding="utf-8"
        )

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
        return state if isinstance(state, dict) else {}

    def save_ui_state(self, state: dict) -> None:
        """Write the UI state (best effort; it is only a convenience)."""
        try:
            self.ui_state_path.write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
