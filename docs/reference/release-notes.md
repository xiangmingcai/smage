# Release notes

The authoritative changelog lives in
[CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).

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
