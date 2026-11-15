"""Extension registry definitions (Qt-free).

The Extension Manager and the segmentation page both read this table.
"""
from __future__ import annotations


EXTENSION_DEFS = [
    {
        "id": "cellpose-cpu",
        "name": "Cellpose-CPU",
        "family": "cellpose",
        "device": "cpu",
        "pip": "cellpose",
        "deps": ["torch", "torchvision"],
        "runner": "cellpose_runner.py",
        "model": "cpsam",
        "desc": "Cellpose (SAM-based) cell segmentation on CPU",
    },
    {
        "id": "cellpose-gpu",
        "name": "Cellpose-GPU",
        "family": "cellpose",
        "device": "gpu",
        "pip": "cellpose",
        "deps": ["torch", "torchvision"],
        "runner": "cellpose_runner.py",
        "model": "cpsam",
        "desc": "Cellpose (SAM-based) cell segmentation on GPU (CUDA)",
    },
    {
        "id": "stardist-cpu",
        "name": "StarDist-CPU",
        "family": "stardist",
        "device": "cpu",
        "pip": "stardist",
        "deps": ["tensorflow"],
        "runner": "stardist_runner.py",
        "model": "2D_versatile_fluo",
        "desc": "StarDist star-convex cell segmentation on CPU",
    },
    {
        # FlowSOM (GPL-3.0) is never bundled with Smage: the user installs it
        # into a separate Python environment and the Cluster module calls it
        # as an external process.
        "id": "flowsom",
        "name": "FlowSOM",
        "family": "flowsom",
        "device": "cpu",
        "pip": "flowsom",
        "deps": ["scanpy"],
        "runner": "flowsom_runner.py",
        "desc": "FlowSOM clustering (SOM + consensus meta-clustering) "
                "for the Cluster module",
    },
    {
        # PARC brings native extensions (hnswlib, pybind11) that nothing else
        # in Smage needs, so it also lives in an external environment.
        "id": "parc",
        "name": "PARC",
        "family": "parc",
        "device": "cpu",
        "pip": "parc",
        "deps": ["hnswlib"],
        "runner": "parc_runner.py",
        "desc": "PARC graph clustering (HDBSCAN + community detection) "
                "for the Cluster module",
    },
]


def _extension_def(ext_id: str) -> dict | None:
    """Return the registry entry for an extension id, or None."""
    for entry in EXTENSION_DEFS:
        if entry["id"] == ext_id:
            return entry
    return None

