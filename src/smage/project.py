"""Project management for Smage.

A Smage project is a folder named ``<name>.smage`` containing:

- ``project.json`` — a small manifest holding the project metadata, the sample
  list, and the workflow (the task tree with every module's parameters).
- ``data/`` — normalized tabular data files (one Parquet per imported sample).

Only the "Data Import" module persists data. Downstream modules store no files:
their results are computed on demand from ``data/`` by replaying the workflow
chain (lazy recomputation).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_VERSION = "0.1.0"
DATA_DIRNAME = "data"
MANIFEST_FILENAME = "project.json"


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
        return project

    def save(self) -> None:
        """Write the project manifest to ``project.json``."""
        manifest = {
            "name": self.name,
            "version": PROJECT_VERSION,
            "created": self.created,
            "samples": self.samples,
            "workflow": self.workflow,
        }
        (self.path / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
