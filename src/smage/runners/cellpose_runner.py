"""Standalone Cellpose runner, executed by Smage's external Python environment.

This file only depends on numpy and cellpose — it must never import from the
``smage`` package. Smage writes the input images and a JSON manifest (a list of
``{"image": in.npy, "output": out.npy}``) and this script segments them all in
one process, so PyTorch is imported and the model is loaded only once.

Usage:
  python cellpose_runner.py check
  python cellpose_runner.py download-model --model cpsam
  python cellpose_runner.py segment --manifest jobs.json --nucleus 0 \
      [--cyto 1] [--model cpsam] [--device cpu] [--threads 8] \
      [--wait-inputs] [--input-timeout 1800]
"""
import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

_DEFAULT_THREADS = max(1, min(8, os.cpu_count() or 1))


def _patch_symlink():
    """Work around libraries creating symlinks for pretrained models.

    On Windows, creating a symlink requires administrator rights or Developer
    Mode (otherwise it fails with WinError 1314). When a symlink cannot be
    created, fall back to copying the target instead.
    """
    real = getattr(os, "symlink", None)
    if real is None or getattr(real, "_smage_fallback", False):
        return

    def safe_symlink(target, link, *args, **kwargs):
        try:
            return real(target, link, *args, **kwargs)
        except OSError:
            if os.path.exists(link) or os.path.lexists(link):
                return
            src = target if os.path.isabs(target) else os.path.join(
                os.path.dirname(os.path.abspath(link)), target
            )
            if os.path.isdir(src):
                shutil.copytree(src, link, dirs_exist_ok=True)
            else:
                parent = os.path.dirname(link)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                shutil.copy2(src, link)

    safe_symlink._smage_fallback = True
    os.symlink = safe_symlink


_patch_symlink()


def check():
    import cellpose
    print(cellpose.__version__)


def download_model(model, threads):
    import torch
    torch.set_num_threads(max(1, int(threads)))
    from cellpose import models
    models.CellposeModel(model_type=model)
    print("model ready:", model)


def _failed_marker(image_path):
    """Path of the marker Smage writes when rasterization fails."""
    return image_path[:-4] + ".failed" if image_path.endswith(".npy") else image_path + ".failed"


def _wait_for_input(image_path, timeout):
    """Wait for an atomically published input, or a failure marker.

    Returns ``(ready, reason)``; ``ready`` is False on failure/timeout.
    """
    marker = _failed_marker(image_path)
    deadline = time.monotonic() + max(1.0, float(timeout))
    while True:
        if os.path.exists(image_path):
            return True, ""
        if os.path.exists(marker):
            try:
                with open(marker, "r", encoding="utf-8") as fh:
                    return False, fh.read().strip() or "input preparation failed"
            except OSError:
                return False, "input preparation failed"
        if time.monotonic() >= deadline:
            return False, "timed out waiting for the input image"
        time.sleep(0.1)


def segment(manifest_path, nucleus, cyto, model, threads, device="cpu",
            wait_inputs=False, input_timeout=1800.0):
    import torch
    torch.set_num_threads(max(1, int(threads)))
    from cellpose import models
    with open(manifest_path, "r", encoding="utf-8") as fh:
        jobs = json.load(fh)
    m = models.CellposeModel(model_type=model, gpu=(device == "gpu"))
    print(f"device: {m.device}", flush=True)
    for job in jobs:
        image_path = job["image"]
        if wait_inputs:
            ready, reason = _wait_for_input(image_path, input_timeout)
            if not ready:
                print(
                    f"failed: {job['output']}|reason={reason}", flush=True
                )
                continue
        try:
            img = np.load(image_path)  # (C, H, W) float array
            # The input is no longer needed once it is in memory.
            try:
                os.remove(image_path)
            except OSError:
                pass
            if cyto is not None:
                stack = img[[cyto, nucleus]]
            else:
                stack = img[nucleus:nucleus + 1]
            t0 = time.perf_counter()
            masks, _flows, _styles = m.eval(stack)
            elapsed = time.perf_counter() - t0
            masks = np.asarray(masks, dtype=np.int32)
            np.save(job["output"], masks)
            print(
                f"done: {job['output']}|cells={int(masks.max())}|time={elapsed:.1f}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"failed: {job['output']}|reason={exc}", flush=True)


def main(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check")

    download = sub.add_parser("download-model")
    download.add_argument("--model", default="cpsam")
    download.add_argument("--threads", type=int, default=_DEFAULT_THREADS)

    seg = sub.add_parser("segment")
    seg.add_argument("--manifest", required=True)
    seg.add_argument("--nucleus", type=int, required=True)
    seg.add_argument("--cyto", type=int, default=None)
    seg.add_argument("--model", default="cpsam")
    seg.add_argument("--device", default="cpu")
    seg.add_argument("--threads", type=int, default=_DEFAULT_THREADS)
    seg.add_argument("--wait-inputs", action="store_true",
                     help="wait for each input file to appear")
    seg.add_argument("--input-timeout", type=float, default=1800.0)

    args = parser.parse_args(argv)
    if args.command == "check":
        check()
    elif args.command == "download-model":
        download_model(args.model, args.threads)
    elif args.command == "segment":
        segment(
            args.manifest, args.nucleus, args.cyto, args.model, args.threads,
            args.device, args.wait_inputs, args.input_timeout,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
