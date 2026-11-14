# Third-Party Notices

Smage uses the following third-party software. Each component is distributed
under its own license; the corresponding copyright notice, license text and
(where applicable) `NOTICE` file must be retained when Smage is redistributed.
This file is a summary — the authoritative text lives in each package's own
`LICENSE`/`NOTICE` file (for the Python packages listed here, inside the
`.dist-info` folder of the installed wheel).

## Runtime dependencies

| Component | Version | License | Note |
|---|---|---|---|
| PySide6 (Qt for Python) | 6.8.3 | LGPL-3.0-only (also GPL-2.0 / GPL-3.0 / commercial) | Linking against the LGPL Qt libraries is permitted in both open-source and proprietary applications; keep the LGPL text and note any Qt modifications. |
| pyqtgraph | 0.14.0 | MIT | |
| numpy | 2.4.6 | BSD 3-Clause | Some vendored components carry their own permissive notices (see `numpy/LICENSE.txt`). |
| pandas | 3.0.6 | BSD 3-Clause | Copyright (c) 2008-2011 AQR Capital Management, LLC, Lambda Foundry, Inc. and PyData Development Team; Copyright (c) 2011-2026 open source contributors. |
| scipy | 1.17.1 | BSD 3-Clause | Copyright (c) 2001-2002 Enthought, Inc.; 2003 SciPy Developers. |
| pyarrow | 26.0.0 | Apache License 2.0 | Include the full Apache 2.0 license text and its `NOTICE`. |
| tifffile | 2026.3.3 | BSD 3-Clause | |
| flowio | 1.4.0 | BSD 3-Clause | Copyright (c) 2019, Scott White. |
| umap-learn | 0.5.12 | BSD 3-Clause | |
| scikit-learn | 1.9.1 | BSD 3-Clause | |
| scanpy | 1.11.5 | BSD 3-Clause | Used by the Cluster module's PhenoGraph / Leiden methods. |
| igraph | 0.11.9 | GPL-2.0-or-later | Graph backend required by scanpy's Louvain/Leiden. |
| leidenalg | 0.12.0 | GPL-3.0 | Leiden community detection for scanpy. |
| openTSNE | 1.0.4 | BSD 3-Clause | Optional t-SNE backend for the Dimensionality Reduction module. |
| python-louvain (`community`) | 0.16 | BSD 3-Clause | Louvain implementation used through scanpy. |
| louvain (C extension) | 0.8.2 | GPL-3.0-or-later | C implementation used by python-louvain. Compatible with Smage's GPL-3.0; when distributing the bundle, point to its corresponding source (upstream repository or sdist). |
| peacoqc-rs | 0.1.2 | MIT | PeacoQC quality control used by Data Clean (imported as `peacoqc`). |
| polars | 2.1.0 | MIT | DataFrame engine required by peacoqc-rs. |
| matplotlib | 3.11.2 | Matplotlib License (BSD-style) | Batch-alignment report figures (SVG/PDF/PNG). |

## Optional extensions (installed by the user, not bundled)

These are installed by the user into a separate Python environment from Smage's
Extension Manager. They are distributed by their own authors, under their own
licenses, and are not part of the Smage installer.

| Component | License | Note |
|---|---|---|
| FlowSOM (Python package `flowsom`) | GPL-3.0 | Used by the Cluster module as an external process. Smage does not bundle or redistribute it; the user installs it with pip in a separate environment. |
| scanpy / igraph / leidenalg (FlowSOM's dependencies) | BSD-3 / GPL-2.0-or-later / GPL-3.0 | Also installed by the user when setting up the FlowSOM extension. |
| PARC (Python package `parc`) | MIT | Used by the Cluster module as an external process. Not bundled; the user installs it in the same kind of separate environment. |
| hnswlib / pybind11 (PARC's native dependencies) | Apache-2.0 / BSD-3 | Installed with PARC in the user's environment; not part of the Smage installer. |
| SAM 3 (`facebook/sam3` code and weights) | SAM License (Meta, 2025-11-19) | Used by the Region module as an external process. Smage does not bundle or redistribute the code or weights; the user installs them in a separate environment and accepts Meta's license, which permits commercial and research use but restricts military, nuclear, espionage and weapons use. Research publications must acknowledge SAM. |

## Build-time only (not bundled into the application)

| Component | Version | License | Note |
|---|---|---|---|
| PyInstaller | 6.22.3 | GPL-2.0-or-later with a special linking exception | Used only to package Smage; not distributed as part of the application. |

## Planned / optional (machine-learning segmentation methods)

These are not yet bundled. When added, keep the license text alongside them and
confirm the licensing of any pretrained model weights before redistributing.

| Component | License | Note |
|---|---|---|
| StarDist | BSD 3-Clause | Copyright and license text to be copied from https://github.com/stardist/stardist. |
| TensorFlow | Apache License 2.0 | Include the full Apache 2.0 license text and its `NOTICE`. Pretrained model files may carry separate terms — verify before redistribution. |

## Compliance summary

- **BSD / MIT components**: retain their copyright notice and disclaimer.
- **Apache 2.0 components** (pyarrow, TensorFlow): also ship the full license
  text and any `NOTICE`, and state the changes you made.
- **GPL components** (igraph, leidenalg, louvain): compatible with Smage's
  GPL-3.0, but their complete corresponding source must be available when the
  application is redistributed — link to each upstream repository or sdist in
  the release notes.
- **LGPL components** (PySide6/Qt): keep the LGPL text and make the Qt libraries
  replaceable (standard dynamic linking is sufficient for a Python app).
- **Do not** use the names of the copyright holders to endorse or promote Smage
  without written permission.
