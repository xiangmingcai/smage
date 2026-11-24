"""Shared sampled-row pool and pooled-column cache.

Qt-free: the worker thread communicates through queues; the UI polls
``_pool_poll`` from its timer.
"""
from __future__ import annotations

from smage.core.sampling import (
    _row_priorities,
    _stable_sample_selection,
)
from smage.core.settings import _setting
from smage.runlog import runlog


def _pool_log_hit(ctx, kind, key) -> None:
    """Log the first hit for a cache key in this pool generation."""
    logged = getattr(ctx, "_pool_hit_logged", None)
    if logged is None:
        logged = set()
        ctx._pool_hit_logged = logged
    marker = (kind, key)
    if marker in logged:
        return
    logged.add(marker)
    if kind == "rows":
        runlog.info("pool.hit", kind=kind, file=str(key[0]), tier=int(key[1]))
    else:
        runlog.info(
            "pool.hit", kind=kind, file=str(key[0]),
            task=str(key[1]), column=str(key[2]), tier=int(key[3]),
        )


def _pool_tier(ctx, max_points=None) -> int:
    """Sampling tier for a plot's draw limit.

    The pool keeps 20x the drawn point count, capped at 300k; it never
    keeps fewer rows than the plot can actually draw.  Passing the plot's
    own max_points matters: an N×N card draws only ~1k points, while a
    normal scatter may draw 50k.
    """
    if max_points is None:
        max_points = _setting("plot", "max_points", 50000)
    try:
        limit = max(1, int(max_points or 50000))
    except (TypeError, ValueError):
        limit = 50000
    need = min(300_000, limit * 20)
    need = max(limit, need)
    for tier in ctx._POOL_TIERS:
        if tier >= need:
            return tier
    return ctx._POOL_TIERS[-1]


def _pool_stats_tier(ctx) -> int:
    """Stable sampling tier for Gate statistics, independent of plot limits."""
    return 300_000


def _pool_rows_cached(ctx, parquet_path, tier):
    return ctx._pool_rows_cache.get((str(parquet_path), int(tier)))


def _pool_rows(ctx, parquet_path, max_points=None):
    """Row indices of the pool (priority order), requesting them if needed."""
    tier = ctx._pool_tier(max_points)
    key = (str(parquet_path), tier)
    cached = ctx._pool_rows_cache.get(key)
    if cached is not None:
        _pool_log_hit(ctx, "rows", key)
        return cached
    if ("rows", key) not in ctx._pool_failed:
        ctx._pool_queue_job(("rows", key))
    return None


def _pool_column(ctx, parquet_path, task_id, column, max_points=None):
    tier = ctx._pool_tier(max_points)
    key = (str(parquet_path), str(task_id or ""), str(column), tier)
    cached = ctx._pool_col_cache.get(key)
    if cached is not None:
        try:
            ctx._pool_col_order.remove(key)
        except ValueError:
            pass
        ctx._pool_col_order.append(key)
        _pool_log_hit(ctx, "col", key)
        return cached
    if ("col", key) not in ctx._pool_failed:
        ctx._pool_queue_job(("col", key))
    return None


def _pool_register(ctx, job, callback) -> None:
    """Run `callback` when pooled data has progressed.

    Waiters are fired once per poll, not per finished job: a plot that
    still misses a column simply shows its placeholder and registers
    again, instead of re-rendering once for every column that arrives.
    """
    ctx._pool_waiters.append(callback)


def _pool_queue_job(ctx, job) -> None:
    job = (job[0], job[1], ctx._pool_generation)
    if job in ctx._pool_queued:
        return
    ctx._pool_queued.add(job)
    kind, key, _generation = job
    if kind == "rows":
        runlog.info("pool.miss", kind=kind, file=str(key[0]), tier=int(key[1]))
    else:
        runlog.info(
            "pool.miss", kind=kind, file=str(key[0]),
            task=str(key[1]), column=str(key[2]), tier=int(key[3]),
        )
    ctx._pool_start_worker()
    ctx._pool_jobs.put(job)


def _pool_start_worker(ctx) -> None:
    import queue as queue_mod
    import threading

    if ctx._pool_worker is not None:
        return
    if ctx._pool_jobs is None:
        ctx._pool_jobs = queue_mod.Queue()
        ctx._pool_results = queue_mod.Queue()

    def run() -> None:
        pending = []
        stopping = False
        while not stopping:
            job = pending.pop(0) if pending else ctx._pool_jobs.get()
            if job is None:
                break
            batch = [job]
            try:
                kind, key, _generation = job
                if kind == "rows":
                    parquet, tier = key
                    value = ctx._pool_build_indices(parquet, tier)
                    # Row indices are tiny; columns are stored by the GUI
                    # thread so the memory budget stays accurate.
                    ctx._pool_rows_cache[key] = value
                    ctx._pool_results.put((True, job, value))
                    continue
                parquet, task_id, _column, tier = key
                # A plot set queues one job per column.  Drain every queued
                # column for the same file/task/tier so one parquet read
                # serves the whole batch instead of one read per column.
                while True:
                    try:
                        nxt = ctx._pool_jobs.get_nowait()
                    except queue_mod.Empty:
                        break
                    if nxt is None:
                        stopping = True
                        break
                    nkind, nkey, _ = nxt
                    if (
                        nkind == "col"
                        and nkey[0] == parquet
                        and nkey[1] == task_id
                        and nkey[3] == tier
                    ):
                        batch.append(nxt)
                    else:
                        pending.append(nxt)
                _pool_emit_columns(ctx, parquet, task_id, batch)
            except Exception as exc:  # noqa: BLE001
                for item in batch:
                    ctx._pool_results.put((False, item, exc))
                runlog.error(
                    "pool.job_failed", kind=str(batch[0][0]),
                    key=str(batch[0][1]), jobs=len(batch), exc=exc,
                )

    ctx._pool_worker = threading.Thread(target=run, daemon=True)
    ctx._pool_worker.start()
    ctx._pool_timer.start()


def _pool_build_indices(ctx, parquet_path, tier):
    import time

    import numpy as np

    started = time.perf_counter()
    count = int(ctx._data_row_count(parquet_path) or 0)
    if count <= 0:
        raise ValueError("no rows in file")
    if count <= tier:
        indices = np.arange(count, dtype=np.int64)
    else:
        picked = _stable_sample_selection(
            np.ones(count, dtype=bool), int(tier)
        )
        indices = np.flatnonzero(picked)
    if indices.size <= 1:
        runlog.info(
            "pool.rows_build", file=str(parquet_path), tier=int(tier),
            rows=count, sampled=int(indices.size),
            seconds=round(time.perf_counter() - started, 3),
        )
        return indices.astype(np.int64)
    # Priority order: "first K kept rows" then equals the stable sample of
    # K rows over the whole file.
    order = np.argsort(_row_priorities(indices), kind="stable")
    runlog.info(
        "pool.rows_build", file=str(parquet_path), tier=int(tier),
        rows=count, sampled=int(indices.size),
        seconds=round(time.perf_counter() - started, 3),
    )
    return indices[order].astype(np.int64)


def _pool_emit_columns(ctx, parquet_path, task_id, jobs) -> None:
    """Read all columns of one file/task/tier in a single pass.

    The stage pipeline is evaluated on the sampled rows only, so a large
    Compensation result is cached at plot-sample size instead of full file
    size.  When an upstream Subsample changes the row set, fall back to the
    established full-read path.
    """
    import time

    import numpy as np

    started = time.perf_counter()
    tier = int(jobs[0][1][3])
    key = (str(parquet_path), tier)
    indices = ctx._pool_rows_cache.get(key)
    if indices is None:
        indices = ctx._pool_build_indices(parquet_path, tier)
        ctx._pool_rows_cache[key] = indices
    columns = [job[1][2] for job in jobs]
    subsample = ctx._subsample_row_index(task_id, parquet_path)
    sampled = subsample is None
    runlog.info(
        "pool.build_start", file=str(parquet_path), task=str(task_id),
        tier=tier, rows=int(len(indices)), mode="sampled" if sampled else "full",
        columns=columns,
    )
    values = ctx._column_data(
        task_id, parquet_path, columns,
        sample_indices=indices if sampled else None,
    )
    runlog.info(
        "pool.build_done", file=str(parquet_path), task=str(task_id),
        tier=tier, rows=int(len(indices)), mode="sampled" if sampled else "full",
        columns=len(columns), read=len(values),
        seconds=round(time.perf_counter() - started, 3),
    )
    for job in jobs:
        column = job[1][2]
        array = values.get(column)
        if array is None:
            ctx._pool_results.put(
                (False, job, ValueError(f"'{column}' could not be read"))
            )
            continue
        arr = np.asarray(array, dtype="float64")
        if sampled and arr.size == indices.size:
            pooled = arr.astype("float32")
        elif indices.size and int(indices.max()) < arr.size:
            pooled = arr[indices].astype("float32")
        else:
            ctx._pool_results.put(
                (False, job, ValueError(
                    f"'{column}' has {arr.size} rows, expected {indices.size}"
                ))
            )
            continue
        ctx._pool_results.put((True, job, pooled))


def _pool_build_column(ctx, parquet_path, task_id, column, max_points=None):
    import numpy as np

    tier = ctx._pool_tier(max_points)
    key = (str(parquet_path), tier)
    indices = ctx._pool_rows_cache.get(key)
    if indices is None:
        indices = ctx._pool_build_indices(parquet_path, tier)
        ctx._pool_rows_cache[key] = indices
    values = ctx._column_data(task_id, parquet_path, [column])
    array = values.get(column)
    if array is None:
        raise ValueError(f"'{column}' could not be read")
    pooled = np.asarray(array, dtype="float64")[indices]
    return pooled.astype("float32")


def _pool_poll(ctx) -> None:
    import queue as queue_mod

    if ctx._pool_results is None:
        return
    finished = []
    while True:
        try:
            finished.append(ctx._pool_results.get_nowait())
        except queue_mod.Empty:
            break
    generation = ctx._pool_generation
    for ok, job, payload in finished:
        ctx._pool_queued.discard(job)
        if job[2] != generation:
            # The pool was cleared while this job was running.
            continue
        if ok and job[0] == "col":
            ctx._pool_store_column(job[1], payload)
        if not ok:
            ctx._pool_failed.add((job[0], job[1]))
    # Run at most two waiters per poll so a burst of finished columns
    # cannot re-render every waiting plot in one long pass. Waiters left
    # over are served on later polls even when no new job has finished.
    waiters, ctx._pool_waiters = ctx._pool_waiters, []
    batch, rest = waiters[:2], waiters[2:]
    if rest:
        ctx._pool_waiters = rest + ctx._pool_waiters
    for callback in batch:
        try:
            callback()
        except RuntimeError:
            # The plot widget went away while the data was loading.
            pass


def _pool_store_column(ctx, key, array) -> None:
    old = ctx._pool_col_cache.get(key)
    if old is not None:
        ctx._pool_col_bytes -= int(old.nbytes)
        try:
            ctx._pool_col_order.remove(key)
        except ValueError:
            pass
    ctx._pool_col_cache[key] = array
    ctx._pool_col_order.append(key)
    ctx._pool_col_bytes += int(array.nbytes)
    while ctx._pool_col_bytes > ctx._POOL_MAX_BYTES and len(ctx._pool_col_order) > 1:
        oldest = ctx._pool_col_order.pop(0)
        dropped = ctx._pool_col_cache.pop(oldest, None)
        if dropped is not None:
            ctx._pool_col_bytes -= int(dropped.nbytes)
            runlog.info(
                "pool.evict", file=str(oldest[0]), task=str(oldest[1]),
                column=str(oldest[2]), tier=int(oldest[3]),
                bytes=int(dropped.nbytes),
                total_bytes=int(ctx._pool_col_bytes),
            )


def _pool_clear(ctx) -> None:
    runlog.info(
        "pool.clear", rows=int(len(ctx._pool_rows_cache)),
        columns=int(len(ctx._pool_col_cache)),
        bytes=int(ctx._pool_col_bytes),
    )
    ctx._pool_generation += 1
    ctx._pool_rows_cache.clear()
    ctx._pool_col_cache.clear()
    ctx._pool_col_order.clear()
    ctx._pool_col_bytes = 0
    ctx._pool_waiters.clear()
    ctx._pool_failed.clear()
    ctx._pool_queued.clear()
    ctx._pool_hit_logged = set()
    ctx._pool_stage_cache = {}


def _pool_ready(ctx, parquet_path, task_id, columns, max_points=None):
    """(indices, {column: pooled array}) or None when the pool is pending."""
    indices = ctx._pool_rows(parquet_path, max_points=max_points)
    if indices is None:
        return None
    arrays = {}
    for column in columns:
        array = ctx._pool_column(
            parquet_path, task_id, column, max_points=max_points
        )
        if array is None:
            return None
        arrays[column] = array
    return indices, arrays


def _pool_pending_keys(ctx, parquet_path, task_id, columns, max_points=None):
    """Job keys a render must wait for (excludes cached / failed ones)."""
    tier = ctx._pool_tier(max_points)
    path = str(parquet_path)
    keys = []
    if (path, tier) not in ctx._pool_rows_cache:
        keys.append(("rows", (path, tier)))
    for column in columns:
        key = (path, str(task_id or ""), str(column), tier)
        if key not in ctx._pool_col_cache:
            keys.append(("col", key))
    return keys
