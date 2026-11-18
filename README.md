# Smage <img src="docs/assets/logo.png" alt="Smage logo" height="32" align="right">

Smage is a free, open-source desktop application for analyzing **spectral
image and flow-cytometry data**. It imports FCS files and microscopy images
into a common tabular representation and lets you compose analysis pipelines
from interactive modules — gating, compensation, unmixing, segmentation,
feature aggregation, clustering, dimensionality reduction and export.

> **Status:** early development. The current release is `0.3.0`; modules and
> file formats may still change between releases.

> **Intended use:** research software, not a medical device. Not for clinical
> diagnosis or decision-making. Provided without warranty; you are responsible
> for the analysis and its results. See [DISCLAIMER.md](DISCLAIMER.md).

**Documentation:** <https://xiangmingcai.github.io/smage/> ·
**Releases:** <https://github.com/xiangmingcai/smage/releases> ·
**Issues:** <https://github.com/xiangmingcai/smage/issues>

## What's new in 0.3.0

- **Subsample** can sample inside gates shared by every selected file, with a
  new **In gates** column and gate-based percentages and allocation.
- **Data Import** image resize adds linked width/height ratio fields; the
  target limit is now 200 megapixels or the original image size.
- **Figure**, **Gating** and **Calculation** axes offer **Manual**,
  **From scale** and **Auto** ranges, remembered per channel, with a 5 %
  border so the data no longer touches the axis frame.
- **Gating** stays empty until **Data files** are selected; the **Scale**
  preview only lists the selected files.
- Tooltips stay readable on dark Windows themes, and the Unmix action is now
  **Set other gate as spectrum**.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

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
- Optional extensions (Cellpose, StarDist, FlowSOM, PARC) have their own
  Python and package requirements; see [Extensions](#extensions).

## Extensions

Optional analysis methods run in a separate Python environment created from
**Extension → Manage Extensions…**. Smage does not bundle these packages.

| Extension | Used by | Version | Repository |
|---|---|---|---|
| Cellpose (CPU / GPU) | Segmentation | 4.2.1.1 | [MouseLand/cellpose](https://github.com/MouseLand/cellpose) |
| StarDist (CPU) | Segmentation | 0.9.2 | [stardist/stardist](https://github.com/stardist/stardist) |
| FlowSOM | Cluster, Batch Align | 0.2.2 | [saeyslab/FlowSOM_Python](https://github.com/saeyslab/FlowSOM_Python) |
| PARC | Cluster | 0.40 | [ShobiStassen/PARC](https://github.com/ShobiStassen/PARC) |

The Extension Manager installs the current pip release; the versions above
were current when Smage 0.3.0 was released. Use a **Python 3.11** environment
for these extensions, matching the Python version Smage is built with. Each
environment may need extra packages (for example PyTorch for Cellpose or
TensorFlow for StarDist) — see the extension's repository for its
requirements.

## Install

1. Download `Smage-vX.Y.Z-win64.zip` from the
   [Releases](https://github.com/xiangmingcai/smage/releases) page.
2. Extract the whole `Smage` folder somewhere on a local disk.
3. Double-click `Smage.exe`.

The current builds are **not code-signed yet**, so Windows may block the
first start. See **If Windows blocks the app** below. The download page
includes a SHA-256 checksum for verification.

## If Windows blocks the app

Smage is not code-signed yet. If Windows shows "Windows protected your PC":

1. If **More info → Run anyway** is available, use it.
2. If that option is missing, right-click the downloaded zip or `Smage.exe` →
   **Properties → General → Security → Unblock**, apply, then extract/run
   again. PowerShell: `Unblock-File -Path "C:\path\to\Smage.exe"`.
3. If it still does not start, check **Windows Security → App & browser
   control → Smart App Control settings**. On a personal PC, Smart App
   Control can be switched off; on a managed work or university PC, contact
   IT because policy may hide the Run anyway button.

The detailed steps are in
[Windows protected your PC](docs/reference/windows-protected-your-pc.md).

## Code signing

Smage builds are currently **not code-signed**, so Windows SmartScreen may
show "Windows protected your PC" for the portable build. Code signing will
be added once the project has established enough adoption for an approved
signing provider. The planned process, scope and approval rules are in
[CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md); the privacy and network
access statement is in [PRIVACY.md](PRIVACY.md).

Every release includes `SHA256SUMS.txt` so the downloaded archive can be
verified independently.

## Quick start

1. `File → New Project…` and choose a folder; the project is a directory
   named `<name>.smage`.
2. **Data Import**: add your image files and import them.
3. Add modules from the workflow tree. Each module has its own settings,
   a **Save** button and a **Run/Calculate** action where it produces data.
4. Open **Figure** to look at the results; finish with
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
