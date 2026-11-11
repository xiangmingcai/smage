# Changelog

All notable changes to Smage are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- Segmentation (Nuclear Expansion): optional **Split touching nuclei**, which
  seeds each nucleus at its distance-map maximum and assigns pixels to the
  nearest seed, giving clumped nuclei separate labels. A **split minimum
  separation** setting controls how strong the split is (Auto by default).

### Fixed

- Ellipse gates drawn on a log or arcsinh axis now select exactly the shape
  that was drawn. The outline is stored in data units and used for the mask,
  instead of a data-space ellipse that appears as a skewed diamond.

### Changed

- Segmentation: the calculation log is now a shared dark log box below the
  Calculate button, splitting the parameters panel into a 4:1 parameter/log
  layout.
- Segmentation: the log reports progress per file, is saved with the module,
  is restored when the module reopens, and a rerun appends to the history
  instead of clearing it.
- Segmentation: calculations no longer freeze the window. Nuclear Expansion
  runs on a worker thread, and Cellpose / StarDist rasterize their images on
  a worker thread before the external process starts; the Calculate button
  is disabled until the run finishes.

### Planned

- Code signing for the Windows executable and installer (SignPath Foundation).
- A dependency lock file so packaged builds are reproducible.
- `CITATION.cff` / Zenodo DOI for academic citation.

## [0.1.0] — 2026-11-09

First packaged (portable Windows) build.

### Added

- Layered application architecture (`core`, `data`, `widgets`, `modules`,
  `app`) with 21 analysis modules: import, gating, compensation, unmix,
  illumination correction, scale, batch align, rename, subsample, subset,
  segmentation, area, cluster, dimensionality reduction, data clean,
  feature down, feature up, gate2col, heatmap, figure and export.
- Workflow tree with module locking, copy/paste, and a project folder
  (`.smage`) holding data, settings and per-project logs.
- Gating: rectangle, ellipse, polygon, quadrant, split and range gates,
  boolean gates, per-file gate geometry, background editing, and gate
  statistics computed in the background.
- Feature Down: aggregation rules per class, percentile ranges, background
  value filtering, and a default group-key row for the feature variable.
- Figure and Heatmap modules with density/parameter colouring, overlays and
  image layers.
- Portable Windows build: PyInstaller onedir bundle, `--self-test` import
  check and `--run-tests` that runs the bundled regression suite in the
  frozen environment.
- Regression suite (225 cases) runnable with `--smoke/--fast/--full` tiers.

### Changed

- Scale is a **display-only** transform; gates are stored in real data
  coordinates and transformed only when drawn.
- Gate statistics use the plotted sample by default, with an explicit
  "Update statistics with full data" button.

### Fixed

- Gate counts and labels now refresh when a gate drag is released, including
  boolean gates that reference the moved gate.
- Gate editing no longer rebuilds the Unmix matrix on every lookup, which
  made label and statistic refreshes take seconds.
- Pool column reads are batched per file and Unmix stages are skipped for
  generated files.
