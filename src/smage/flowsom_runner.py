"""Standalone FlowSOM runner, executed by Smage's external Python environment.

This file only depends on numpy and flowsom — it must never import from the
``smage`` package. Smage writes the clustering matrix, runs this script, and
reads the result arrays back.

Usage:
  python flowsom_runner.py run --input matrix.npy --output result.npz \
      --xdim 10 --ydim 10 --k 20 --seed 42
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
    from flowsom import FlowSOM

    matrix = np.load(args.input)
    if matrix.ndim != 2:
        raise ValueError("input matrix must be 2-D")
    print(
        f"FlowSOM: {matrix.shape[0]:,} rows x {matrix.shape[1]} variables, "
        f"grid {args.xdim}x{args.ydim}, k={args.k}",
        flush=True,
    )
    fsom = FlowSOM(
        matrix,
        n_clusters=int(args.k),
        xdim=int(args.xdim),
        ydim=int(args.ydim),
        seed=int(args.seed),
    )
    print("FlowSOM: reading metaclusters", flush=True)
    cell = np.asarray(
        fsom.get_cell_data().obs["metaclustering"].to_numpy(dtype=np.int64)
    )
    codes = np.asarray(fsom.model.codes, dtype=np.float32)
    nodes = np.asarray(fsom.model.cluster_labels).astype(np.int64)
    metas = np.asarray(fsom.model.metacluster_labels).astype(np.int64)
    # Every cells of a SOM node share its metacluster, so filling by node is
    # well defined (this is the same mapping Batch Alignment uses).
    node_cluster = np.zeros(codes.shape[0], dtype=np.int32)
    node_cluster[nodes] = metas
    n_clusters = int(node_cluster.max()) + 1 if node_cluster.size else 0
    if cell.size != matrix.shape[0]:
        raise ValueError("FlowSOM returned an unexpected number of labels")
    np.savez(
        args.output,
        cell_cluster=cell,
        node_cluster=node_cluster,
        codes=codes,
        xdim=int(args.xdim),
        ydim=int(args.ydim),
        n_clusters=n_clusters,
    )
    print(f"FlowSOM: wrote {args.output} ({n_clusters} clusters)", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FlowSOM runner for Smage")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="cluster one matrix")
    run_p.add_argument("--input", required=True, help="input .npy matrix")
    run_p.add_argument("--output", required=True, help="output .npz file")
    run_p.add_argument("--xdim", type=int, required=True)
    run_p.add_argument("--ydim", type=int, required=True)
    run_p.add_argument("--k", type=int, required=True)
    run_p.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.command == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
