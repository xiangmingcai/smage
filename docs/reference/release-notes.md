# Release notes

The authoritative changelog lives in
[CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).

## 0.3.1 — 2026-11-19

- **Feature Up**: **Match** pairs files by their visible names, **Save**
  persists the settings, and the log shows mapped names instead of internal
  file ids.
- The spectral and multiplex image workflow guides embed video walkthroughs
  for every step.
- README documents the optional Cellpose, StarDist, FlowSOM and PARC
  extensions, including their Python 3.11 requirement.

See CHANGELOG.md for the complete list.

## 0.3.0 — 2026-11-18

Third release.

- **Subsample** can restrict sampling to gates shared by every selected file,
  with an **In gates** column and gate-based percentages, Total allocation and
  manual overrides.
- **Data Import** image resize gains linked **Width ratio** / **Height ratio**
  fields; the target limit is now 200 megapixels or the original image size.
- **Figure**, **Gating** and **Calculation** plot axes have **Manual**,
  **From scale** and **Auto** range modes, remember settings per channel, and
  leave a 5 % border so the data no longer touches the axis frame.
- **Gating** starts empty until **Data files** are chosen; **Scale** previews
  only offer the files selected in its Data files panel.
- Tooltips inside settings panels and on colour swatches now use the light
  app style on every Windows theme.
- **Unmix**: the gate-tree action is now **Set other gate as spectrum**.
- **Extensions**: the environment path field shows example `python.exe`
  locations. StarDist model downloads verify TLS with the Windows trust store.

See CHANGELOG.md for the complete list.

## 0.2.0 — 2026-11-16

Second release, published under GPL-3.0 with the approval of Amsterdam UMC.

- New **Calculation**, **Region** and **Region Size** modules; the Area
  module is removed.
- Region 0/1 channels, per-file Region detection thresholds, boundary
  editing, multi-selection and a detection log.
- Unmix **Manual input**: paste a matrix copied from another module, edit
  cells and reorder rows; both signature sources are kept.
- Heatmap legend titles and **Save as…** for heatmaps and plots.
- Feature Down **Mode** aggregation and **Add batch**.
- Research-use disclaimer and clearer intended-use wording.

See CHANGELOG.md for the complete list.

## 0.1.0 — 2026-11-09

First packaged (portable Windows) build.

- 21 analysis modules: import, gating, compensation, unmix, illumination
  correction, scale, batch align, rename, subsample, subset, segmentation,
  area, cluster, dimensionality reduction, data clean, feature down, feature
  up, gate2col, heatmap, figure and export.
- Portable PyInstaller bundle with a bundled regression suite
  (`Smage.exe --run-tests`) and import self-check (`Smage.exe --self-test`).
- Gating with rectangle, ellipse, polygon, quadrant, split and range gates,
  boolean gates, per-file geometry and background statistics.
- Scale is display-only; gates are stored in original data units.
- Feature Down includes a deletable group-key row for the feature variable.
