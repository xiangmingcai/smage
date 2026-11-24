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
      [--model 2D_versatile_fluo] [--threads 8] \
      [--wait-inputs] [--input-timeout 1800]
"""
import argparse
import json
import os
import shutil
import ssl
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


def _configure_tls():
    """Trust the OS certificate store (or a user CA bundle) for downloads.

    ``urllib`` in the external environment may not use the same CA bundle as
    ``pip``; behind a corporate TLS proxy this ends in
    ``CERTIFICATE_VERIFY_FAILED``.  Never disable verification: use the
    Windows store through ``truststore``, an explicit CA bundle, or certifi.
    """
    bundle = os.environ.get("SMAGE_CA_BUNDLE") or os.environ.get(
        "SSL_CERT_FILE"
    )
    if bundle and os.path.isfile(bundle):
        context = ssl.create_default_context(cafile=bundle)
        ssl._create_default_https_context = lambda: context
        return
    try:
        import truststore

        truststore.inject_into_ssl()
        return
    except Exception:  # noqa: BLE001 - optional dependency
        pass
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
        ssl._create_default_https_context = lambda: context
    except Exception:  # noqa: BLE001 - fall back to Python's default store
        pass


def check():
    import stardist
    print(stardist.__version__)


def download_model(model, threads):
    _configure_tls()
    _limit_tensorflow_threads(threads)
    from stardist.models import StarDist2D

    try:
        StarDist2D.from_pretrained(model)
    except Exception as exc:  # noqa: BLE001 - add actionable TLS guidance
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            print(
                "TLS certificate verification failed. Install your "
                "organization's root CA in the Windows certificate store, or "
                "set SMAGE_CA_BUNDLE to a PEM file containing it, then retry "
                "the download. Do not disable SSL verification.",
                file=sys.stderr,
                flush=True,
            )
        raise
    print("model ready:", model)


def _failed_marker(image_path):
    """Path of the marker Smage writes when rasterization fails."""
    return image_path[:-4] + ".failed" if image_path.endswith(".npy") else image_path + ".failed"


def _wait_for_input(image_path, timeout):
    """Wait for an atomically published input, or a failure marker."""
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


def segment(manifest_path, nucleus, model, prob, nms, threads, tile_size=0,
            device="cpu", wait_inputs=False, input_timeout=1800.0):
    if device == "cpu":
        # Force CPU even if a GPU-capable TensorFlow is installed; the env var
        # must be set before TensorFlow is imported below.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    _limit_tensorflow_threads(threads)
    _configure_tls()
    print("phase: importing tensorflow", flush=True)
    from csbdeep.utils import normalize
    from stardist.models import StarDist2D
    print("phase: loading model", flush=True)
    model = StarDist2D.from_pretrained(model)
    print("phase: running inference", flush=True)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        jobs = json.load(fh)
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
            img = np.load(image_path)  # (C, H, W) or (H, W) float array
            try:
                os.remove(image_path)
            except OSError:
                pass
            if img.ndim == 3:
                img = img[nucleus]
            # StarDist's pretrained models expect percentile-normalised input;
            # raw intensities would otherwise segment into a few bogus shapes.
            img = normalize(img, 1, 99.8).astype("float32")
            # Tile large images (the default n_tiles=None means "no tiling",
            # which processes the whole image in one slow forward pass).
            # Tiling is also what produces the per-tile progress. n_tiles is a
            # per-axis tuple (rows, columns). With a target tile_size we split
            # each image into roughly size×size tiles by deriving the counts
            # from its dimensions; otherwise use StarDist's own auto guess.
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
        except Exception as exc:  # noqa: BLE001
            print(f"failed: {job['output']}|reason={exc}", flush=True)


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
            args.manifest, args.nucleus, args.model, args.prob, args.nms,
            args.threads, args.tile_size, args.device,
            args.wait_inputs, args.input_timeout,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
