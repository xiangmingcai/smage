"""Standalone StarDist runner, executed by Smage's external Python environment.

This file only depends on numpy and stardist — it must never import from the
``smage`` package. Smage writes the input images and a JSON manifest (a list of
``{"image": in.npy, "output": out.npy}``) and this script segments them all in
one process, so TensorFlow is imported and the model is loaded only once.

Usage:
  python stardist_runner.py check
  python stardist_runner.py download-model --model 2D_versatile_fluo
  python stardist_runner.py segment --manifest jobs.json --nucleus 0 \
      --prob 0.5 --nms 0.3 [--tile-size 0] [--device cpu] \
      [--model 2D_versatile_fluo] [--threads 8]
"""
import argparse
import json
import os
import shutil
import sys
import time

import numpy as np

_DEFAULT_THREADS = max(1, min(8, os.cpu_count() or 1))

# stdout is a pipe here, so Python would block-buffer it; force line buffering
# so the model-loading messages and per-file results reach Smage immediately.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass


def _patch_symlink():
    """Work around csbdeep creating symlinks for pretrained models.

    StarDist's csbdeep library symlinks the extracted model folder, which on
    Windows requires administrator rights or Developer Mode (otherwise it fails
    with WinError 1314). When the symlink cannot be created, fall back to
    copying the target directory instead.
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


def _limit_tensorflow_threads(n):
    """Limit TensorFlow threads before it is imported (avoids oversubscription)."""
    n = max(1, int(n))
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(n)
    os.environ["TF_NUM_INTEROP_THREADS"] = str(n)


def _tile_progress(iterable, disable=False, total=None):
    """tqdm-style progress callback: print `tile: i/N` after each tile.

    StarDist's `predict_instances(show_tile_progress=...)` calls this with the
    tile iterator; yielding through it and printing a newline per tile lets the
    runner stream per-tile progress to Smage.
    """
    i = 0
    for item in iterable:
        yield item
        i += 1
        print(f"tile: {i}/{total}", flush=True)


def check():
    import stardist
    print(stardist.__version__)


def download_model(model, threads):
    _limit_tensorflow_threads(threads)
    from stardist.models import StarDist2D
    StarDist2D.from_pretrained(model)
    print("model ready:", model)


def segment(manifest_path, nucleus, model, prob, nms, threads, tile_size=0, device="cpu"):
    if device == "cpu":
        # Force CPU even if a GPU-capable TensorFlow is installed; the env var
        # must be set before TensorFlow is imported below.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    _limit_tensorflow_threads(threads)
    print("phase: importing tensorflow", flush=True)
    from csbdeep.utils import normalize
    from stardist.models import StarDist2D
    print("phase: loading model", flush=True)
    model = StarDist2D.from_pretrained(model)
    print("phase: running inference", flush=True)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        jobs = json.load(fh)
    for job in jobs:
        img = np.load(job["image"])  # (C, H, W) or (H, W) float array
        if img.ndim == 3:
            img = img[nucleus]
        # StarDist's pretrained models expect percentile-normalised input;
        # raw intensities would otherwise segment into a few bogus shapes.
        img = normalize(img, 1, 99.8).astype("float32")
        # Tile large images (the default n_tiles=None means "no tiling", which
        # processes the whole image in one slow forward pass). Tiling is also
        # what produces the per-tile progress. n_tiles is a per-axis tuple
        # (rows, columns). With a target tile_size we split each image into
        # roughly size×size tiles by deriving the counts from its dimensions;
        # otherwise we use StarDist's own auto guess.
        if tile_size > 0:
            h, w = img.shape[:2]
            tiles_y = max(1, int(np.ceil(h / tile_size)))
            tiles_x = max(1, int(np.ceil(w / tile_size)))
        else:
            tiles_y, tiles_x = model._guess_n_tiles(img)
        t0 = time.perf_counter()
        masks, _detail = model.predict_instances(
            img, prob_thresh=prob, nms_thresh=nms,
            n_tiles=(tiles_y, tiles_x),
            show_tile_progress=_tile_progress,
        )
        elapsed = time.perf_counter() - t0
        masks = np.asarray(masks, dtype=np.int32)
        np.save(job["output"], masks)
        print(
            f"done: {job['output']}|cells={int(masks.max())}|time={elapsed:.1f}",
            flush=True,
        )


def main(argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check")

    download = sub.add_parser("download-model")
    download.add_argument("--model", default="2D_versatile_fluo")
    download.add_argument("--threads", type=int, default=_DEFAULT_THREADS)

    seg = sub.add_parser("segment")
    seg.add_argument("--manifest", required=True)
    seg.add_argument("--nucleus", type=int, required=True)
    seg.add_argument("--model", default="2D_versatile_fluo")
    seg.add_argument("--prob", type=float, default=0.5)
    seg.add_argument("--nms", type=float, default=0.3)
    seg.add_argument("--tile-size", type=int, default=0)
    seg.add_argument("--device", default="cpu")
    seg.add_argument("--threads", type=int, default=_DEFAULT_THREADS)

    args = parser.parse_args(argv)
    if args.command == "check":
        check()
    elif args.command == "download-model":
        download_model(args.model, args.threads)
    elif args.command == "segment":
        segment(
            args.manifest, args.nucleus, args.model, args.prob, args.nms,
            args.threads, args.tile_size, args.device,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
