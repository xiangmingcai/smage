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
| NodeGraphQt | 0.6.44 | MIT | |

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
- **LGPL components** (PySide6/Qt): keep the LGPL text and make the Qt libraries
  replaceable (standard dynamic linking is sufficient for a Python app).
- **Do not** use the names of the copyright holders to endorse or promote Smage
  without written permission.
