"""Background worker threads used by the data and module layers.

Qt-free: they communicate through queues and are polled by timers.
"""
from __future__ import annotations

import queue
import threading
import time

from smage.core.gates import (
    _compute_gate_filter_mask,
    _gate_needed_columns,
    _gate_own_mask,
)
from smage.core.image_ops import _pool_mask
from smage.core.pipeline import (
    _evaluate_stages,
    _pipeline_dependencies,
)
from smage.runlog import runlog


class _ProcessWorker:
    """Background worker that runs one subprocess command to completion.

    Used to run external Python commands (venv creation, pip install, model
    download, verification and segmentation) without blocking the UI.
    """

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._proc = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def run(self, key, argv, cwd=None):
        self._req_q.put((key, [list(argv)], cwd))

    def run_chain(self, key, commands, cwd=None):
        """Run several commands in order; stop at the first failure.

        Used when a step must complete before the next one (upgrading pip
        before installing packages into that environment).
        """
        self._req_q.put((key, [list(cmd) for cmd in commands], cwd))

    def poll(self):
        """Return finished events: ``(key, "line", text)`` for streaming output
        and ``(key, "done", returncode, text)`` when the command completes."""
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def clear(self):
        """Drop any unread results (e.g. from a previous, finished operation)."""
        while True:
            try:
                self._res_q.get_nowait()
            except queue.Empty:
                break

    def kill(self):
        """Terminate the in-flight subprocess (if any)."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _run(self):
        import subprocess

        while not self._stop.is_set():
            try:
                key, commands, cwd = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                chunks = []
                returncode = 0
                for argv in commands:
                    proc = subprocess.Popen(
                        argv,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        # Run without a console window (the external python is
                        # a console app; this hides the flash that would
                        # otherwise pop up on Windows).
                        creationflags=getattr(
                            subprocess, "CREATE_NO_WINDOW", 0
                        ),
                    )
                    self._proc = proc
                    for line in proc.stdout:
                        line = line.rstrip("\r\n")
                        if line:
                            chunks.append(line)
                            self._res_q.put((key, "line", line))
                    proc.wait()
                    returncode = int(proc.returncode)
                    if returncode != 0:
                        break
                self._res_q.put(
                    (key, "done", returncode, "\n".join(chunks))
                )
            except Exception as exc:  # noqa: BLE001
                self._res_q.put((key, "done", -1, str(exc)))
            finally:
                self._proc = None


class _GatingStatsWorker:
    """Background worker that computes gate statistics with column/mask caches.

    The worker owns its caches so no shared mutable state is touched by the
    main thread. Each request carries a generation number so the main thread
    can discard stale results, and only the newest pending request is kept:
    drawing gates quickly must not queue up a backlog of full recomputes.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._pending = None
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._columns = {}   # {parquet_path: {col: float64 ndarray}}
        self._current_path = None
        self._masks = {}     # {gate_id: population bool ndarray}
        self._counts = {}    # {gate_id: int}
        # Compensation/Unmix outputs are properties of the data pipeline,
        # not of the gate shapes: evaluate them once per (file, pipeline)
        # and reuse them for every subsequent gate adjustment.
        self._stage_cache = {}
        self._stage_sig = None
        self._row_key = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, generation, parquet_path, gates, stages, base_columns,
                affected, virtual=None, row_index=None, stage_sig=None):
        with self._cond:
            prev = self._pending
            if prev is not None:
                # Coalesce with the request that has not started yet. A full
                # recompute stays full; otherwise the affected gates merge.
                if prev[5] is None or affected is None:
                    affected = None
                else:
                    affected = set(prev[5]) | set(affected)
            self._pending = (
                generation, parquet_path, gates, stages, set(base_columns),
                affected, dict(virtual or {}), row_index, stage_sig,
            )
            self._cond.notify()

    def poll(self):
        """Return any finished (generation, stats) results."""
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def _take_request(self):
        with self._cond:
            while self._pending is None and not self._stop.is_set():
                self._cond.wait(0.2)
            req = self._pending
            self._pending = None
            return req

    def _run(self):
        while not self._stop.is_set():
            req = self._take_request()
            if req is None:
                continue
            try:
                res = self._compute(*req)
            except Exception as error:  # noqa: BLE001
                # A silent failure here leaves the gate counts stale with no
                # hint why, so record it before dropping the result.
                try:
                    runlog.error("gating.stats_error", exc=error)
                except Exception:  # noqa: BLE001 - logging must not break
                    pass
                res = None
            self._res_q.put(res)

    def _compute(self, generation, parquet_path, gates, stages, base_columns,
                 affected, virtual=None, row_index=None, stage_sig=None):
        import numpy as np
        import pandas as pd
        if not parquet_path or not gates:
            return (generation, {})
        if self._current_path != parquet_path:
            self._columns = {}
            self._masks = {}
            self._counts = {}
            self._stage_cache = {}
            self._stage_sig = None
            self._row_key = None
            self._current_path = parquet_path
        row_key = None
        if row_index is not None:
            try:
                row_key = (
                    len(row_index), int(row_index[0]),
                    int(row_index[-1]), int(row_index.sum()),
                )
            except (IndexError, TypeError, ValueError):
                row_key = (len(row_index),)
        if row_key != self._row_key:
            # Switching between the display sample and the full data
            # invalidates masks; their arrays have different lengths.
            self._masks = {}
            self._counts = {}
            self._stage_cache = {}
            self._row_key = row_key
        if stage_sig != self._stage_sig:
            # A saved upstream module changed the data pipeline.
            self._stage_cache = {}
            self._stage_sig = stage_sig
        if affected is None:
            # Full recompute: the gate set may have changed, so reset caches.
            self._masks = {}
            self._counts = {}
        by_id = {g["id"]: g for g in gates}
        needed = set()
        for g in gates:
            needed |= _gate_needed_columns(g, by_id)
        if not needed:
            return (generation, {})
        dependencies = _pipeline_dependencies(stages, needed, parquet_path)
        base_needed = [c for c in dependencies if c in base_columns]
        cols = self._columns
        missing = set(base_needed) - set(cols)
        if missing:
            try:
                df = pd.read_parquet(parquet_path, columns=sorted(missing))
            except Exception:
                return (generation, {})
            for name in missing:
                cols[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
            self._columns = cols
        arrays = {name: cols[name] for name in base_needed if name in cols}
        for name, values_arr in (virtual or {}).items():
            arrays.setdefault(name, values_arr)
        if row_index is not None and arrays:
            # An upstream Subsample module limits the visible rows.
            n_rows = len(next(iter(arrays.values())))
            arrays = {
                name: (arr[row_index] if len(arr) == n_rows else arr)
                for name, arr in arrays.items()
            }
        arrays = _evaluate_stages(
            stages, arrays, needed, parquet_path, cache=self._stage_cache
        )
        existing = set(arrays)
        if not arrays:
            return (generation, {})
        n_total = len(next(iter(arrays.values())))
        by_parent = {}
        for g in gates:
            by_parent.setdefault(g.get("parent"), []).append(g)

        def own_mask(g):
            return _gate_own_mask(
                g, by_id, lambda name: arrays[name], {}, existing
            )

        def compute_subtree(gid):
            stack = [gid]
            while stack:
                cur = stack.pop()
                g = by_id.get(cur)
                if g is None:
                    continue
                om = own_mask(g)
                if om is None:
                    self._masks[cur] = None
                    self._counts[cur] = None
                    continue
                parent_id = g.get("parent")
                pm = self._masks.get(parent_id) if parent_id else None
                m = om if pm is None else (pm & om)
                self._masks[cur] = m
                self._counts[cur] = int(np.sum(m))
                for child in by_parent.get(cur, []):
                    stack.append(child["id"])

        if affected is None:
            for top in by_parent.get(None, []):
                compute_subtree(top["id"])
        else:
            affected_set = set(affected)
            tops = [
                gid for gid in affected_set
                if by_id.get(gid, {}).get("parent") not in affected_set
            ]
            for gid in tops:
                compute_subtree(gid)

        result = {}
        for g in gates:
            c = self._counts.get(g["id"])
            if c is None:
                continue
            pid = g.get("parent")
            if pid:
                pc = self._counts.get(pid)
                if pc is None:
                    pc = n_total
            else:
                pc = n_total
            pct = (c / pc * 100.0) if pc else 0.0
            result[g["id"]] = (c, pct)
        return (generation, result)


class _IlluminationWorker:
    """Background worker that estimates an illumination mask from tiles."""

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, key, stages, base_columns, files, channel, x_name,
                y_name, tile_w, tile_h, bins, resolution, generated=None):
        self._req_q.put((
            key, stages, base_columns, list(files), channel, x_name, y_name,
            int(tile_w), int(tile_h), int(bins), int(resolution),
            dict(generated or {}),
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                req = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = self._compute(*req)
            except Exception as exc:  # noqa: BLE001
                res = (req[0], None, f"Could not calculate the mask: {exc}")
            self._res_q.put(res)

    def _compute(self, key, stages, base_columns, files, channel, x_name,
                 y_name, tile_w, tile_h, bins, resolution, generated=None):
        import numpy as np
        import pandas as pd
        from scipy.interpolate import RectBivariateSpline, griddata

        generated = generated or {}
        if not files:
            return (key, None, "No files selected for the calculation.")
        n_blocks = bins * bins
        sums = np.zeros(n_blocks, dtype="float64")
        counts = np.zeros(n_blocks, dtype="float64")
        used_files = 0
        small_files = 0
        for path in files:
            gen = generated.get(path)
            # A generated channel (for example a saved Unmix output) lives in
            # its own row-aligned parquet; only x/y come from the source.
            needed = {x_name, y_name} if gen else {x_name, y_name, channel}
            dependencies = _pipeline_dependencies(stages, needed, path)
            base = base_columns.get(path) or set()
            if not base:
                try:
                    import pyarrow.parquet as pq
                    base = set(pq.ParquetFile(path).schema.names)
                except Exception:  # noqa: BLE001
                    continue
            base = [c for c in dependencies if c in base]
            values = {}
            if base:
                try:
                    df = pd.read_parquet(path, columns=base)
                except Exception:  # noqa: BLE001
                    continue
                for name in base:
                    values[name] = pd.to_numeric(
                        df[name], errors="coerce"
                    ).to_numpy(dtype="float64")
            values = _evaluate_stages(stages, values, needed, path)
            if not all(name in values for name in needed):
                continue
            x = np.asarray(values[x_name], dtype="float64")
            y = np.asarray(values[y_name], dtype="float64")
            if gen:
                try:
                    gdf = pd.read_parquet(
                        gen["path"], columns=[gen["column"]]
                    )
                except Exception:  # noqa: BLE001
                    continue
                v = pd.to_numeric(
                    gdf[gen["column"]], errors="coerce"
                ).to_numpy(dtype="float64")
                if v.size != x.size:
                    continue
            else:
                v = np.asarray(values[channel], dtype="float64")
            finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(v)
            x, y, v = x[finite], y[finite], v[finite]
            if v.size == 0:
                continue
            x0 = float(x.min())
            y0 = float(y.min())
            width = float(x.max() - x0) + 1.0
            height = float(y.max() - y0) + 1.0
            ntx = int(width // tile_w)
            nty = int(height // tile_h)
            if ntx < 1 or nty < 1:
                small_files += 1
                continue
            ti = np.floor((x - x0) / tile_w).astype(np.int64)
            tj = np.floor((y - y0) / tile_h).astype(np.int64)
            keep = (ti >= 0) & (ti < ntx) & (tj >= 0) & (tj < nty)
            if not keep.any():
                continue
            x, y, v = x[keep], y[keep], v[keep]
            ti, tj = ti[keep], tj[keep]
            tile_id = tj * ntx + ti
            wx = (x - x0) - ti * tile_w
            wy = (y - y0) - tj * tile_h
            bx = np.clip((wx * bins / tile_w).astype(np.int64), 0, bins - 1)
            by = np.clip((wy * bins / tile_h).astype(np.int64), 0, bins - 1)
            block = by * bins + bx
            frame = pd.DataFrame({"tile": tile_id, "block": block, "v": v})
            # 10th percentile threshold per tile; darker points are background.
            thresholds = frame.groupby("tile")["v"].quantile(0.1)
            frame["thr"] = frame["tile"].map(thresholds)
            frame = frame[frame["v"] >= frame["thr"]]
            if frame.empty:
                continue
            # Median per block within each tile, then mean over tiles.
            medians = frame.groupby(["tile", "block"])["v"].median()
            per_block = medians.groupby(level="block").mean()
            indices = per_block.index.to_numpy(dtype=np.int64)
            sums[indices] += per_block.to_numpy()
            counts[indices] += 1.0
            used_files += 1

        if used_files == 0 or not (counts > 0).any():
            if small_files and small_files == len(files):
                return (
                    key, None,
                    "The tile size is larger than the selected image(s): "
                    "no complete tile fits. Reduce the tile width/height.",
                )
            return (key, None, "No tile data was found for the selected parameters.")

        grid = np.full(n_blocks, np.nan)
        valid = counts > 0
        grid[valid] = sums[valid] / counts[valid]
        grid = grid.reshape(bins, bins)

        # Smooth the bin x bin fields to resolution x resolution, then to a
        # full tile-sized mask.
        gy, gx = np.mgrid[0:bins, 0:bins]
        flat_valid = valid.reshape(-1)
        points = np.column_stack([gx.ravel()[flat_valid],
                                  gy.ravel()[flat_valid]])
        values_grid = grid.reshape(-1)[flat_valid]
        target_x = np.linspace(0, bins - 1, resolution)
        target_y = np.linspace(0, bins - 1, resolution)
        tx, ty = np.meshgrid(target_x, target_y)
        smooth = griddata(points, values_grid, (tx, ty), method="linear")
        if np.isnan(smooth).any():
            nearest = griddata(points, values_grid, (tx, ty), method="nearest")
            smooth = np.where(np.isnan(smooth), nearest, smooth)
        spline = RectBivariateSpline(
            np.arange(resolution), np.arange(resolution), smooth, kx=1, ky=1
        )
        mask = spline(
            np.linspace(0, resolution - 1, tile_h),
            np.linspace(0, resolution - 1, tile_w),
        )
        mask = np.asarray(mask, dtype="float64")
        if mask.shape != (tile_h, tile_w):
            return (key, None, "Could not build the mask at the requested size.")
        return (key, {
            "mask": mask.tolist(),
            "tile_w": int(tile_w),
            "tile_h": int(tile_h),
            "bin": int(bins),
            "resolution": int(resolution),
            "files": list(files),
        }, None)


class _ImageFilterWorker:
    """Background worker that computes gate-filter masks for Image plots."""

    def __init__(self):
        self._req_q = queue.Queue()
        self._res_q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def request(self, key, parquet_path, gates, gate_id, stages, base_columns,
                height, width, blocks, origin=None):
        self._req_q.put((
            key, parquet_path, gates, gate_id, stages, set(base_columns),
            height, width, blocks, origin,
        ))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self._res_q.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                req = self._req_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = self._compute(*req)
            except Exception as exc:  # noqa: BLE001
                res = (req[0], f"Filter could not be applied: {exc}")
            self._res_q.put(res)

    def _compute(self, key, parquet_path, gates, gate_id, stages, base_columns,
                 height, width, blocks, origin=None):
        import numpy as np
        import pandas as pd
        started = time.perf_counter()
        # A Region gate is purely spatial: rasterise its rings instead of
        # testing every image pixel against every ring.  This avoids reading
        # millions of x/y rows and is several times faster.
        from smage.core.gates import _gate_chain
        chain = _gate_chain(gates, gate_id)
        if (
            len(chain) == 1
            and chain[0].get("type") == "region"
            and origin is not None
        ):
            from smage.core.region_detection import _region_parts_raster
            mask_2d = _region_parts_raster(
                chain[0].get("parts") or [],
                int(width), int(height),
                float(origin[0]), float(origin[1]),
            ).astype(np.uint8)
            levels = _pool_mask(mask_2d, blocks)
            runlog.info(
                "image.filter",
                mode="region",
                file=str(key[0]),
                seconds=round(time.perf_counter() - started, 3),
                pixels=int(mask_2d.sum()),
            )
            return (key, {"mask_2d": mask_2d, "levels": levels})
        by_id = {g["id"]: g for g in gates}
        needed = set()
        for g in gates:
            needed |= _gate_needed_columns(g, by_id)
        dependencies = _pipeline_dependencies(stages, needed, parquet_path)
        base = [c for c in dependencies if c in base_columns]
        values = {}
        if base:
            try:
                df = pd.read_parquet(parquet_path, columns=base)
            except Exception as exc:  # noqa: BLE001
                return (key, f"Filter could not be applied: {exc}")
            for name in base:
                values[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).to_numpy(dtype="float64")
        values = _evaluate_stages(stages, values, needed, parquet_path)
        try:
            mask = _compute_gate_filter_mask(
                gates, gate_id, parquet_path, {},
                existing=set(values), extra=values,
            )
        except KeyError as exc:
            return (key, f"Filter parameter not found: {exc}")
        except Exception as exc:  # noqa: BLE001
            return (key, f"Filter could not be applied: {exc}")
        if mask is None:
            return (key, "Filter could not be computed (gate has no usable parameters)")
        mask = np.asarray(mask, dtype=np.uint8)
        total = int(height) * int(width)
        if mask.size != total:
            return (key, f"Filter mask size mismatch ({mask.size} vs {total})")
        mask_2d = mask.reshape(int(height), int(width))
        levels = _pool_mask(mask_2d, blocks)
        runlog.info(
            "image.filter",
            mode="gate",
            file=str(key[0]),
            seconds=round(time.perf_counter() - started, 3),
            pixels=int(mask_2d.sum()),
        )
        return (key, {"mask_2d": mask_2d, "levels": levels})


