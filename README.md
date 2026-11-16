<p align="center">
  <img src="docs/assets/logo.png" alt="Smage logo" width="140">
</p>

# Smage

Smage is a free, open-source desktop application for analyzing **spectral
image and flow-cytometry data**. It imports FCS files and microscopy images
into a common tabular representation and lets you compose analysis pipelines
from interactive modules — gating, compensation, unmixing, segmentation,
feature aggregation, clustering, dimensionality reduction and export.

> **Status:** early development. The current release is `0.2.0`; modules and
> file formats may still change between releases.

> **Intended use:** research software, not a medical device. Not for clinical
> diagnosis or decision-making. Provided without warranty; you are responsible
> for the analysis and its results. See [DISCLAIMER.md](DISCLAIMER.md).

**Documentation:** <https://xiangmingcai.github.io/smage/> ·
**Releases:** <https://github.com/xiangmingcai/smage/releases> ·
**Issues:** <https://github.com/xiangmingcai/smage/issues>

## Modules

Every analysis step is a module in the workflow tree, and downstream modules
see the data produced by the ones above them.

| Group | Modules |
|---|---|
| Input | Data Import (FCS, image stacks, tables) |
| Map raw values | Compensation, Unmix, Illumination Correction, Batch Align, Calculation, Rename |
| Display only | Scale (changes how values are drawn, never the data) |
| Sampling | Subsample, Subset |
| Gating | Gating (rect / ellipse / polygon / quadrant / split / range gates, boolean gates) |
| Image analysis | Segmentation (Cellpose, StarDist, classical thresholding), Region (Signal Expansion) |
| Analyse | Region Size (pixel area per Region) |
| Features | Feature Down (aggregate per class), Feature Up, Gate2Col |
| Computation | Cluster, Dimensionality Reduction, Data Clean |
| Output | Figure, Heatmap, Export |

## Requirements

- **Windows 10 or 11, 64-bit.**
- No Python installation is required for the packaged build.

## Install

1. Download `Smage-vX.Y.Z-win64.zip` from the
   [Releases](https://github.com/xiangmingcai/smage/releases) page.
2. Extract the whole `Smage` folder somewhere on a local disk.
3. Double-click `Smage.exe`.

The current builds are **not code-signed yet**, so Windows SmartScreen may
show "Windows protected your PC" the first time. Choose **More info → Run
anyway**. The download page includes a SHA-256 checksum for verification.

## Code signing

Code signing is being arranged through **SignPath Foundation**. Until it is
active, Windows SmartScreen may show "Windows protected your PC" for the
portable build. The process, scope and approval rules are described in
[CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md). The privacy and network
access statement is in [PRIVACY.md](PRIVACY.md).

Once signing is active, release downloads will state:
"Free code signing provided by SignPath.io, certificate by SignPath
Foundation."

## Quick start

1. `File → New Project…` and choose a folder; the project is a directory
   named `<name>.smage`.
2. **Data Import**: add your `.fcs` or image files and import them.
3. Add modules from the workflow tree. Each module has its own settings,
   a **Save** button and a **Run/Calculate** action where it produces data.
4. Open **Figure** or **Heatmap** to look at the results; finish with
   **Export** to write CSV/parquet or images.

## Where your data lives

- **Everything is processed locally.** Smage does not upload your data.
- The project folder holds the imported data, module settings, generated
  files and a diagnostic log in `<project>.smage/log/`. Please attach the
  newest log when reporting a problem.
- Global settings live in `%APPDATA%\Smage\` (`settings.json`,
  `extensions.json`); they are not part of a project.

## Build from source

Requirements: Python 3.11, 64-bit.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pythonw src\smage\main.py
```

Run the regression suite:

```bat
.venv\Scripts\python.exe tests\regression\run_all.py --full
```

Build the portable Windows bundle (PyInstaller + self-test + frozen e2e):

```bat
.venv\Scripts\python.exe packaging\build_win.py
```

The result is written to `dist\Smage\`.

## Contributing

Bug reports and feature requests are welcome in the
[issue tracker](https://github.com/xiangmingcai/smage/issues). Pull requests
are accepted; commits must be signed off (DCO) — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- Author and maintainer: **Xiangming Cai**
- Advisor: **Juan J. Garcia Vallejo**
- Developed at **Amsterdam UMC**, the Netherlands.

See [AUTHORS.md](AUTHORS.md) for the full list and [NOTICE](NOTICE) for the
copyright statement.

## License

Smage is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License, version 3** (GPL-3.0).
See [LICENSE](LICENSE) for the full text.

Smage is released under GPL-3.0 with the approval of Amsterdam UMC, the
Netherlands.

Copyright (C) 2026 Amsterdam UMC, the Netherlands.

Third-party components keep their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The corresponding source
code of every release is available in this repository (and in the source
archive attached to each GitHub release).

## Contact

- Author: Xiangming Cai (Amsterdam UMC)
- Email: xiangming_cai@126.com
- Issues: <https://github.com/xiangmingcai/smage/issues>
