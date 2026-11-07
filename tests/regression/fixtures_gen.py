"""Generate deterministic synthetic fixtures for the regression suite.

All fixtures are small, reproducible, and free of real experimental data, so
the suite runs on a fresh machine or CI without copying user files.  Real data
is optional and lives under ``_fixtures/real`` (see ``--real-data``).

Usage::

    python tests/regression/fixtures_gen.py --root E:\\smage_test\\samge_batch_test
    python tests/regression/fixtures_gen.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_fcs(path: Path, events: np.ndarray, names: list[str],
               descriptions: list[str]) -> None:
    import flowio

    with path.open("wb") as handle:
        flowio.create_fcs(
            handle,
            np.asarray(events, dtype=np.float32).ravel().tolist(),
            names,
            descriptions,
            {"$CYTSOURCE": "Smage synthetic"},
        )


def generate(root: Path, force: bool = False) -> dict:
    """Create every fixture under *root* and return a manifest."""
    root = Path(root)
    tables = root / "tables"
    fcs = root / "fcs"
    images = root / "images"
    broken = root / "broken"
    for folder in (tables, fcs, images, broken):
        folder.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"version": 1, "files": []}

    def record(path: Path, kind: str, **info) -> None:
        manifest["files"].append(
            {
                "path": path.relative_to(root).as_posix(),
                "kind": kind,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                **info,
            }
        )

    rng = np.random.default_rng(42)

    # --- tabular data -----------------------------------------------------
    base_path = tables / "base.parquet"
    base_csv = tables / "base.csv"
    if force or not base_path.is_file() or not base_csv.is_file():
        n = 5000
        index = np.arange(n)
        base = pd.DataFrame(
            {
                "x": (index % 100).astype("int32"),
                "y": (index // 100).astype("int32"),
                "FSC-A": rng.normal(50_000, 12_000, n).astype("float32"),
                "SSC-A": rng.normal(30_000, 8_000, n).astype("float32"),
            }
        )
        for channel in range(1, 9):
            base[f"FL{channel}-A"] = rng.gamma(2.0, 100.0, n).astype("float32")
        base.to_parquet(base_path, index=False)
        base.to_csv(base_csv, index=False)
    record(base_path, "table", rows=5000, columns=list(pd.read_parquet(base_path).columns))
    record(tables / "base.csv", "table", rows=5000, columns=list(pd.read_csv(tables / "base.csv", nrows=0).columns))

    edge_path = tables / "edge.parquet"
    if force or not edge_path.is_file():
        edge = pd.DataFrame(
            {
                "x": np.arange(6),
                "y": np.arange(6),
                "zero": [0.0, -1.0, np.nan, 1e9, -1e9, 0.0],
                "constant": 7,
                "unicode 列": np.arange(6),
                "spaced name": np.arange(6),
            }
        )
        edge.to_parquet(edge_path, index=False)
        edge.to_csv(tables / "edge.csv", index=False)
    record(edge_path, "table", rows=6, columns=list(pd.read_parquet(edge_path).columns))

    tiny_path = tables / "tiny.parquet"
    if force or not tiny_path.is_file():
        pd.DataFrame({"x": [0], "y": [0], "c1": [1.0]}).to_parquet(tiny_path, index=False)
    record(tiny_path, "table", rows=1, columns=["x", "y", "c1"])

    empty_path = tables / "empty.parquet"
    if force or not empty_path.is_file():
        pd.DataFrame({"x": pd.Series([], dtype="int32"), "y": pd.Series([], dtype="int32"),
                      "c1": pd.Series([], dtype="float32")}).to_parquet(empty_path, index=False)
    record(empty_path, "table", rows=0, columns=["x", "y", "c1"])

    # --- FCS --------------------------------------------------------------
    fcs8 = fcs / "synthetic_8ch.fcs"
    if force or not fcs8.is_file():
        names = [f"FL{i}-A" for i in range(1, 9)]
        events = rng.gamma(2.0, 100.0, size=(2000, 8))
        _write_fcs(fcs8, events, names, [f"Channel {i}" for i in range(1, 9)])
    record(fcs8, "fcs", rows=2000, channels=8)

    fcs3 = fcs / "synthetic_3ch.fcs"
    if force or not fcs3.is_file():
        names = ["FSC-A", "SSC-A", "FL1-A"]
        events = rng.gamma(2.0, 100.0, size=(300, 3))
        _write_fcs(fcs3, events, names, ["Forward scatter", "Side scatter", "Fluorescence 1"])
    record(fcs3, "fcs", rows=300, channels=3)

    # --- images -----------------------------------------------------------
    multi = images / "multichannel.tif"
    if force or not multi.is_file():
        stack = rng.integers(0, 255, size=(3, 64, 64), dtype=np.uint8)
        tifffile.imwrite(multi, stack, metadata={"axes": "CYX"})
    record(multi, "image", channels=3, shape=[3, 64, 64])

    single = images / "single.tif"
    if force or not single.is_file():
        plane = rng.integers(0, 255, size=(64, 64), dtype=np.uint8)
        tifffile.imwrite(single, plane)
    record(single, "image", channels=1, shape=[64, 64])

    mask = images / "mask.tif"
    if force or not mask.is_file():
        labels = np.zeros((64, 64), dtype=np.uint16)
        labels[10:30, 10:30] = 1
        labels[35:55, 35:55] = 2
        tifffile.imwrite(mask, labels)
    record(mask, "mask", shape=[64, 64])

    # --- deliberately broken inputs --------------------------------------
    truncated = broken / "truncated.fcs"
    if force or not truncated.is_file():
        data = fcs8.read_bytes()
        truncated.write_bytes(data[:512])
    record(truncated, "broken", reason="truncated FCS")

    empty_csv = broken / "empty.csv"
    if force or not empty_csv.is_file():
        empty_csv.write_text("", encoding="utf-8")
    record(empty_csv, "broken", reason="empty CSV")

    bad_parquet = broken / "bad.parquet"
    if force or not bad_parquet.is_file():
        bad_parquet.write_bytes(b"this is not a parquet file")
    record(bad_parquet, "broken", reason="invalid Parquet")

    wrong_ext = broken / "table.txt"
    if force or not wrong_ext.is_file():
        wrong_ext.write_text("x,y\n1,2\n", encoding="utf-8")
    record(wrong_ext, "broken", reason="unsupported extension")

    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def verify(root: Path, repo: Path) -> list[str]:
    """Validate the generated fixtures through the application's readers."""
    sys.path.insert(0, str(repo / "src"))
    from smage.dimensions import read_dimensions
    from smage.importer import import_file

    root = Path(root)
    problems: list[str] = []

    def expect(path: Path, **fields):
        try:
            info = read_dimensions(path)
        except Exception as exc:  # noqa: BLE001 - reported as a problem
            problems.append(f"{path.name}: read_dimensions failed: {exc}")
            return None
        for key, value in fields.items():
            if info.get(key) != value:
                problems.append(f"{path.name}: {key} expected {value!r}, got {info.get(key)!r}")
        return info

    expect(root / "tables" / "base.parquet", kind="table", rows=5000)
    expect(root / "tables" / "base.csv", kind="table", rows=5000)
    expect(root / "tables" / "edge.parquet", kind="table", rows=6)
    expect(root / "tables" / "tiny.parquet", kind="table", rows=1)
    expect(root / "tables" / "empty.parquet", kind="table", rows=0)
    expect(root / "fcs" / "synthetic_8ch.fcs", kind="fcs", rows=2000)
    expect(root / "fcs" / "synthetic_3ch.fcs", kind="fcs", rows=300)
    expect(root / "images" / "multichannel.tif", kind="image", channels=3)
    expect(root / "images" / "single.tif", kind="image", channels=1)

    out = root.parent / "runs" / "_verify" / "out.parquet"
    try:
        info = read_dimensions(root / "tables" / "base.parquet")
        params = [{"name": name, "extract": True} for name in info["columns"]]
        result = import_file(root / "tables" / "base.parquet", params, "all", 100, out)
        if result["rows"] != 5000:
            problems.append(f"import_file parquet: expected 5000 rows, got {result['rows']}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"import_file parquet failed: {exc}")

    try:
        info = read_dimensions(root / "fcs" / "synthetic_8ch.fcs")
        params = [{"name": name, "extract": True} for name in info["columns"]]
        result = import_file(root / "fcs" / "synthetic_8ch.fcs", params, "all", 100, out)
        if result["rows"] != 2000:
            problems.append(f"import_file FCS: expected 2000 rows, got {result['rows']}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"import_file FCS failed: {exc}")

    for name in ("truncated.fcs", "empty.csv", "bad.parquet", "table.txt"):
        try:
            read_dimensions(root / "broken" / name)
        except Exception:
            pass
        else:
            problems.append(f"broken/{name}: expected an error, none was raised")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.environ.get("SMAGE_TEST_ROOT") or r"E:\smage_test\samge_batch_test",
        help="workspace root (fixtures are written to <root>/_fixtures)",
    )
    parser.add_argument("--force", action="store_true", help="regenerate existing files")
    parser.add_argument("--verify", action="store_true", help="validate the fixtures")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    fixtures = root / "_fixtures"
    manifest = generate(fixtures, force=args.force)
    print(f"fixtures: {fixtures} ({len(manifest['files'])} files)")

    if args.verify:
        repo = Path(__file__).resolve().parents[2]
        problems = verify(fixtures, repo)
        for problem in problems:
            print(f"PROBLEM: {problem}")
        print("FIXTURES OK" if not problems else f"FIXTURES: {len(problems)} problem(s)")
        return 0 if not problems else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
