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


def _pool_tier(ctx) -> int:
    limit = _setting("plot", "max_points", 50000)
    need = max(300_000, int(limit or 50000) * 20)
    for tier in ctx._POOL_TIERS:
        if tier >= need:
            return tier
    return ctx._POOL_TIERS[-1]


def _pool_rows_cached(ctx, parquet_path, tier):
    return ctx._pool_rows_cache.get((str(parquet_path), int(tier)))


def _pool_rows(ctx, parquet_path):
    """Row indices of the pool (priority order), requesting them if needed."""
    tier = ctx._pool_tier()
    key = (str(parquet_path), tier)
    cached = ctx._pool_rows_cache.get(key)
    if cached is not None:
        return cached
    if ("rows", key) not in ctx._pool_failed:
        ctx._pool_queue_job(("rows", key))
    return None


def _pool_column(ctx, parquet_path, task_id, column):
    key = (str(parquet_path), str(task_id or ""), str(column))
    cached = ctx._pool_col_cache.get(key)
    if cached is not None:
        try:
            ctx._pool_col_order.remove(key)
        except ValueError:
            pass
        ctx._pool_col_order.append(key)
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
        while True:
            job = ctx._pool_jobs.get()
            if job is None:
                break
            try:
                kind, key, _generation = job
                if kind == "rows":
                    parquet, tier = key
                    value = ctx._pool_build_indices(parquet, tier)
                    # Row indices are tiny; columns are stored by the GUI
                    # thread so the memory budget stays accurate.
                    ctx._pool_rows_cache[key] = value
                else:
                    parquet, task_id, column = key
                    value = ctx._pool_build_column(parquet, task_id, column)
                ctx._pool_results.put((True, job, value))
            except Exception as exc:  # noqa: BLE001
                ctx._pool_results.put((False, job, exc))

    ctx._pool_worker = threading.Thread(target=run, daemon=True)
    ctx._pool_worker.start()
    ctx._pool_timer.start()


def _pool_build_indices(ctx, parquet_path, tier):
    import numpy as np

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
        return indices.astype(np.int64)
    # Priority order: "first K kept rows" then equals the stable sample of
    # K rows over the whole file.
    order = np.argsort(_row_priorities(indices), kind="stable")
    return indices[order].astype(np.int64)


def _pool_build_column(ctx, parquet_path, task_id, column):
    import numpy as np

    tier = ctx._pool_tier()
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


def _pool_clear(ctx) -> None:
    ctx._pool_generation += 1
    ctx._pool_rows_cache.clear()
    ctx._pool_col_cache.clear()
    ctx._pool_col_order.clear()
    ctx._pool_col_bytes = 0
    ctx._pool_waiters.clear()
    ctx._pool_failed.clear()
    ctx._pool_queued.clear()


def _pool_ready(ctx, parquet_path, task_id, columns):
    """(indices, {column: pooled array}) or None when the pool is pending."""
    indices = ctx._pool_rows(parquet_path)
    if indices is None:
        return None
    arrays = {}
    for column in columns:
        array = ctx._pool_column(parquet_path, task_id, column)
        if array is None:
            return None
        arrays[column] = array
    return indices, arrays


def _pool_pending_keys(ctx, parquet_path, task_id, columns):
    """Job keys a render must wait for (excludes cached / failed ones)."""
    tier = ctx._pool_tier()
    path = str(parquet_path)
    keys = []
    if (path, tier) not in ctx._pool_rows_cache:
        keys.append(("rows", (path, tier)))
    for column in columns:
        key = (path, str(task_id or ""), str(column))
        if key not in ctx._pool_col_cache:
            keys.append(("col", key))
    return keys
