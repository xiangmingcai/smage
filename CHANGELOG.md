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
- Scale **Min / Max** now act as the default axis range for downstream
  scatter and histogram plots (Figure, N×N cells, Gating, Area) while their
  axis is **Auto**. The nearest upstream Scale wins; a configured side is
  combined with the data extent on the other, and Overlay plots are excluded.
  Ranges saved in a plot always take precedence.
- Image plots show a live cursor readout on a row under the X axis name:
  pixel x/y and the value of every visible layer at that pixel. Values come
  from the raster already used to draw the plot (no disk reads, no extra
  full-image cache): exact pixels at deep zoom, block means when zoomed out.
  The readout hides while a gate is being drawn or dragged.

### Fixed

- Image plots: LOD refreshes no longer re-apply unchanged axis labels,
  titles and tick styles. On an aspect-locked view that re-layout could
  change the range, schedule another refresh and loop forever (visible as
  high-frequency flicker and runaway CPU/memory use, followed by a crash
  elsewhere).
- Export: dragging a channel row onto another row no longer loses rows. The
  list reports the drop and the page reorders its own parameters, so Qt's
  internal move (which can remove the dragged row) is never used; the list
  is rebuilt from the reordered data. Heatmap's channel list gains the text
  fallback for the same drag-and-drop role loss.
- Ellipse gates drawn on a log or arcsinh axis now select exactly the shape
  that was drawn. The outline is stored in data units and used for the mask,
  instead of a data-space ellipse that appears as a skewed diamond.

### Changed

- Illumination Correction: zooming or panning a preview re-samples the
  image, so magnified views stay sharp instead of showing the initial
  low-resolution raster.
- Illumination Correction: a **Sync view →** / **Sync view ←** button
  below the previews copies the zoom and position of the preview under the
  pointer to the other one. The arrow shows the direction that will be used.
  It replaces the live view link, which could crash while wheel-zooming.
- Scale: an explanatory line at the top of the page states that the module
  only changes visualisation and gate display, never the stored data.
- Export: selecting files no longer preselects every channel automatically;
  the user chooses the channels with **Select channels…**.
- Locked modules: **Unlock** on a module that was not edited no longer
  reloads the page. The untouched draft is adopted as the real module, so
  large modules open immediately instead of being rebuilt.
- Unmix: moving a gate no longer freezes the window. The spectrum is
  recomputed on a worker thread, only the spectra that read the edited tree
  lose their cache, and image plots keep their zoom across re-renders. The
  channel values of the gate tree's file are cached (bounded to two column
  sets), so a gate edit only re-applies the mask instead of re-reading
  millions of rows.
- Unmix: **Samples to be unmixed** now uses the shared multi-select dialog
  (**Select files…**) instead of a checklist with All/None buttons.
- Segmentation: the method chooser now lists Nuclear Expansion, StarDist,
  Cellpose GPU, Cellpose CPU and Mask Input in that order, and Cellpose CPU
  explains under the chooser that it is much slower than the GPU variant.
- Workflow tree: the **Data Import** card no longer offers Copy, Copy
  branch, Delete or Delete branch — it is the fixed root of the workflow.
  **Paste** and **Paste branch** stay (they add a copied module or branch
  at the top level); Rename and Lock remain.
- Terminology: measurement columns are now called **channels** in the UI —
  Segmentation (Select Files), Feature Down (Feature channel / Source
  channel), Batch Alignment, Area, Gate2Col (Convert table, From gate, New
  channel name), Feature Up (Channels to map, Channel in From File),
  Dimension Reduction, Cluster, Heatmap and Export. Unmix uses **Positive
  spectrum / Negative spectrum** in the signature table and **Signature
  matrix / Signature curves** for its matrix tabs. Illumination Correction
  uses **Illumination pattern**, **Files for illumination pattern
  recognition** and **Recognize Illumination Pattern**.
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
