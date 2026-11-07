"""Standalone PARC runner, executed by Smage's external Python environment.

This file only depends on numpy and parc — it must never import from the
``smage`` package. Smage writes the clustering matrix, runs this script, and
reads the labels back.

Usage:
  python parc_runner.py run --input matrix.npy --output result.npz \
      --knn 30 --resolution 1.0 --seed 42
"""
import argparse
import sys

import numpy as np

# stdout is a pipe here, so Python would block-buffer it; force line buffering
# so progress reaches Smage immediately.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001
    pass


def run(args) -> int:
    from parc import PARC

    matrix = np.load(args.input)
    if matrix.ndim != 2:
        raise ValueError("input matrix must be 2-D")
    print(
        f"PARC: {matrix.shape[0]:,} rows x {matrix.shape[1]} variables, "
        f"knn={args.knn}, resolution={args.resolution}",
        flush=True,
    )
    model = PARC(
        matrix,
        knn=int(args.knn),
        resolution_parameter=float(args.resolution),
        random_seed=int(args.seed),
    )
    model.run_PARC()
    labels = np.asarray(model.labels, dtype=np.int64)
    if labels.size != matrix.shape[0]:
        raise ValueError("PARC returned an unexpected number of labels")
    n_clusters = int(labels.max()) + 1 if labels.size else 0
    np.savez(args.output, labels=labels, n_clusters=n_clusters)
    print(f"PARC: wrote {args.output} ({n_clusters} clusters)", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PARC runner for Smage")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="cluster one matrix")
    run_p.add_argument("--input", required=True, help="input .npy matrix")
    run_p.add_argument("--output", required=True, help="output .npz file")
    run_p.add_argument("--knn", type=int, required=True)
    run_p.add_argument("--resolution", type=float, default=1.0)
    run_p.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.command == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
