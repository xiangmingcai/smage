"""L3: project manifest, column index, UI state and mask persistence."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness  # noqa: E402

from smage.project import Project  # noqa: E402


def _new(case_id: str, name: str = "demo") -> Project:
    parent = harness.RUN_DIR / "cases" / case_id
    shutil.rmtree(parent, ignore_errors=True)
    parent.mkdir(parents=True, exist_ok=True)
    return Project.create(parent, name)


@harness.case("project.roundtrip", "L3", "project", tags=("smoke",))
def roundtrip() -> None:
    project = _new("project_roundtrip", "demo")
    project.samples = [{"name": "a.fcs"}]
    project.workflow["tasks"][0]["children"].append(
        {"id": "scale-1", "type": "scale", "name": "Scale",
         "params": {"columns": []}, "children": []}
    )
    project.save()

    loaded = Project.load(project.path)
    harness.eq(loaded.name, "demo")
    harness.eq(loaded.samples, [{"name": "a.fcs"}])
    harness.eq(
        loaded.workflow["tasks"][0]["children"][0]["id"], "scale-1"
    )
    harness.eq(loaded.created, project.created)
    harness.check(
        project.path.name == "demo.smage", f"folder name: {project.path.name}"
    )
    harness.check((project.path / "project.json").is_file())
    harness.check(project.data_dir.is_dir())


@harness.case("project.columns", "L3", "project", tags=("smoke",))
def column_index() -> None:
    project = _new("project_columns")
    harness.eq(project.load_columns(), {})

    project.save_columns({"a.parquet": ["x", "y"]})
    harness.eq(project.load_columns(), {"a.parquet": ["x", "y"]})

    project.columns_path.write_text("{not json", encoding="utf-8")
    harness.eq(project.load_columns(), {}, "a broken index is ignored")


@harness.case("project.ui_state", "L3", "project", tags=("smoke",))
def ui_state() -> None:
    project = _new("project_ui")
    harness.eq(project.load_ui_state(), {})

    project.save_ui_state({"tree": {"expanded": ["a"]}})
    harness.eq(project.load_ui_state(), {"tree": {"expanded": ["a"]}})

    project.ui_state_path.write_text("[1, 2]", encoding="utf-8")
    harness.eq(project.load_ui_state(), {}, "non-dict state is ignored")

    project.ui_state_path.write_text("broken", encoding="utf-8")
    harness.eq(project.load_ui_state(), {}, "broken state is ignored")


@harness.case("project.masks", "L3", "project", tags=("smoke",))
def masks() -> None:
    project = _new("project_masks")
    project.workflow["tasks"][0]["children"].append({
        "id": "illum-1",
        "type": "illumination",
        "name": "Illumination",
        "params": {
            "submodules": [{
                "id": "sub-1",
                "channel": "C",
                "mask": [[1.0, 2.0], [3.0, 4.0]],
            }],
        },
        "children": [],
    })
    project.save()

    manifest = json.loads((project.path / "project.json").read_text(encoding="utf-8"))
    submodule = manifest["workflow"]["tasks"][0]["children"][0]["params"]["submodules"][0]
    harness.check("mask" not in submodule, "the mask is not stored in JSON")
    harness.eq(submodule["mask_file"], "Module/illum-1/sub-1.npy")
    mask_path = project.path / submodule["mask_file"]
    harness.check(mask_path.is_file(), "the mask .npy is written")

    loaded = Project.load(project.path)
    restored = (
        loaded.workflow["tasks"][0]["children"][0]["params"]["submodules"][0]
    )
    harness.eq(restored["mask"], [[1.0, 2.0], [3.0, 4.0]])
    harness.check("mask_file" not in restored)

    mask_path.unlink()
    missing = Project.load(project.path)
    restored = (
        missing.workflow["tasks"][0]["children"][0]["params"]["submodules"][0]
    )
    harness.eq(restored["mask"], None, "a missing mask file is tolerated")


@harness.case("project.errors", "L3", "project", tags=("smoke",))
def load_errors() -> None:
    parent = harness.RUN_DIR / "cases" / "project_errors"
    shutil.rmtree(parent, ignore_errors=True)
    parent.mkdir(parents=True, exist_ok=True)

    with harness.raises(ValueError, match="project.json"):
        Project.load(parent)

    folder = parent / "broken.smage"
    folder.mkdir()
    (folder / "project.json").write_text("{not json", encoding="utf-8")
    with harness.raises(ValueError):
        Project.load(folder)


if __name__ == "__main__":
    raise SystemExit(harness.main())
