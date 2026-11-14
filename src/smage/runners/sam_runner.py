"""Run SAM 3 in an external Python environment (Smage Region module).

Smage never imports torch/transformers itself.  This script is launched with
the extension environment's python.exe:

    python sam_runner.py download-model --model facebook/sam3
    python sam_runner.py run --input image.png --output masks.npz ...

The run mode writes a compressed mask stack (bit-packed) and scores.  Turning
masks into polygons and applying the detection gate stays in Smage, so the
external environment only needs SAM's own dependencies.
"""
from __future__ import annotations

import argparse
import sys


def _device(value: str):
    """Return the Transformers pipeline device index (0 = first GPU)."""
    value = (value or "auto").strip().lower()
    if value in ("cpu", "-1"):
        return -1
    if value.isdigit():
        return int(value)
    try:
        import torch

        return 0 if torch.cuda.is_available() else -1
    except Exception:  # noqa: BLE001
        return -1


def _load_generator(model: str, device: str):
    from transformers import pipeline

    return pipeline("mask-generation", model=model, device=_device(device))


def _download(args) -> int:
    print(f"Downloading {args.model} …", flush=True)
    _load_generator(args.model, "cpu")
    print("Model ready.", flush=True)
    return 0


def _run(args) -> int:
    import numpy as np
    from PIL import Image

    image = Image.open(args.input).convert("RGB")
    print(f"Loading SAM 3 ({args.model}) on device {args.device} …", flush=True)
    generator = _load_generator(args.model, args.device)
    options = {
        "points_per_batch": int(args.points_per_batch),
        "points_per_side": int(args.points_per_side),
        "pred_iou_thresh": float(args.pred_iou),
        "stability_score_thresh": float(args.stability),
        "crop_n_layers": int(args.crop_layers),
        "min_mask_region_area": int(args.min_area),
    }
    print("Generating masks …", flush=True)
    try:
        outputs = generator(image, **options)
    except TypeError:
        # Older/newer pipeline signatures may not accept every option.
        outputs = generator(
            image,
            points_per_batch=int(args.points_per_batch),
        )
    def _numpy(value):
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "numpy"):
            value = value.numpy()
        return np.asarray(value)

    masks = outputs.get("masks") or outputs.get("mask") or []
    scores = outputs.get("scores")
    arrays = []
    for mask in masks:
        array = _numpy(mask)
        if array.dtype != bool:
            array = array > 0
        if array.ndim != 2:
            continue
        arrays.append(array)
    if not arrays:
        np.savez_compressed(
            args.output,
            count=np.array(0),
            shape=np.array([0, 0], dtype=np.int64),
            masks=np.zeros((0, 0), dtype=np.uint8),
            scores=np.zeros((0,), dtype="float32"),
        )
        print("No masks generated.", flush=True)
        return 0
    shape = arrays[0].shape
    arrays = [a for a in arrays if a.shape == shape]
    stack = np.stack(arrays, axis=0)
    packed = np.packbits(stack, axis=-1)
    if scores is None:
        score_array = np.ones((len(arrays),), dtype="float32")
    else:
        score_array = _numpy(scores).astype("float32", copy=False)
        if score_array.size != len(arrays):
            score_array = np.ones((len(arrays),), dtype="float32")
    np.savez_compressed(
        args.output,
        count=np.array(len(arrays), dtype=np.int64),
        shape=np.asarray(shape, dtype=np.int64),
        masks=packed,
        scores=score_array,
    )
    print(f"Wrote {len(arrays)} mask(s) to {args.output}", flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    download = sub.add_parser("download-model")
    download.add_argument("--model", default="facebook/sam3")
    download.set_defaults(func=_download)

    run = sub.add_parser("run")
    run.add_argument("--input", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--model", default="facebook/sam3")
    run.add_argument("--device", default="auto")
    run.add_argument("--points-per-side", type=int, default=32)
    run.add_argument("--points-per-batch", type=int, default=64)
    run.add_argument("--pred-iou", type=float, default=0.88)
    run.add_argument("--stability", type=float, default=0.95)
    run.add_argument("--crop-layers", type=int, default=0)
    run.add_argument("--min-area", type=int, default=0)
    run.set_defaults(func=_run)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - printed to the Smage log
        print(f"SAM runner failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
