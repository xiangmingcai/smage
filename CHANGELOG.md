# Changelog

All notable changes to Smage are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **Region** (new map module): detects image regions and exposes each one to
  downstream modules as a spatial gate. It provides an internal detection-gate
  tree, **Signal Expansion** and **SAM 3** (external environment) detection,
  a Region → Sample → Sub-region tree with per-item visibility, rename,
  delete and branch copy/paste, and per-Region fill/edge drawing styles.
  Sub-region polygons live on the full image and are available to Figure
  filters/image overlays, Gate2Col 0/1 columns, Heatmap annotations and
  Region Size. Internal detection gates support **boolean gates** (NOT /
  AND / OR) and **per-file geometry** (adjust a gate separately on each
  file, or copy one file's shape to all).
- **Region Size** (new module, replaces Area): calculates the pixel area of
  each selected Region in each selected image file as a file × Region table.
  Missing Samples are **NA**, empty Samples are **0**, and overlapping
  sub-regions are counted once. Rows and columns can be reordered, the table
  is copied as TSV, and **Calculate** saves the result and locks upstream
  modules until **Clear results** is used.
- Extension Manager: **SAM 3** entry. It installs torch/transformers into a
  separate environment and downloads the gated `facebook/sam3` weights; Smage
  never bundles them.
- **Calculation** (new map module): builds new channels from existing ones
  with an ordered list of calculation rows — **Clip**, real-value **Scale**
  (Log/Asinh), weighted **Add**, and a custom **Contrast** curve. A row may
  use a channel produced by a row above it, apply to a subset of the files,
  and be hidden from downstream modules while still feeding later rows. The
  module writes no data: saved rows become a map stage evaluated on demand,
  so every module below it sees the new channels; unsaved edits appear only
  in the live preview.
- Data Import: image files can be **resized while importing**. The Parameter
  panel gains an **Image resize** group with Width/Height (the height follows
  the width when **Keep aspect ratio** is on), and a **Smooth
  (anti-aliased)** / **Nearest neighbor** choice. The original TIFF is not
  changed; only the imported pixel table is smaller (or larger). Resizing
  replaces random sampling for images, which is now disabled for them.
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
- Figure: right-click a plot and choose **Save as…** to write the plot as
  it is currently shown, zoom included. **PDF** keeps axes, tick labels,
  titles and the image colour legend as editable text and vector lines,
  while the plot area is embedded as a high-resolution bitmap; **PNG** and
  **JPEG** are written at three times the screen resolution. The right-click
  menu now opens from the plot itself, not only from the card margins.

### Fixed

- Gating: polygon gates drawn on a log or arcsinh axis now use a densely
  sampled outline in data space, so the mask matches the edges drawn on
  screen. Previously only the corner vertices were stored, and the straight
  data-space polygon selected a different set of points.
- Gating: opening a NOT/AND/OR gate subset keeps the source gate shapes
  visible, so the selected points can be compared with the polygon or
  ellipse that defines them. Image gate subsets are rasterised at full pixel
  resolution, so block-mean pooling can no longer show pixels from inside a
  polygon in a NOT subset.
- Segmentation: the distance-map peaks used by **Split touching nuclei**
  are smoothed first, so the ridge along a touching neck is no longer
  detected as an extra nucleus.
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
- Gating / Unmix: reloading an ellipse gate (including the copy created by
  **Duplicate** on a spectrum) keeps a normal ellipse with its handles. The
  stored mask outline is no longer drawn as dozens of polygon anchors; it is
  used only to compute the selection.
- Figure: picking a new color for an Image channel now refreshes that
  channel's swatch in the settings table as well as the legend. Previously
  the swatch kept the old color until the panel was rebuilt.
- Figure / Calculation: Image plots now always label their axes **x** and
  **y** (pixel coordinates). The selected channel is only the colour layer
  and the cursor value, so it can no longer appear as an axis name and the
  left axis no longer shows "Count".
- Calculation: switching the preview from Scatter to Image now renders the
  calculated channel. Pipeline dependency resolution was treating the
  produced channel as a source column, so the image raster request asked
  the parquet file for a column that does not exist and gave up.

### Removed

- The **Area** module was removed; use **Region** + **Region Size** instead.

### Changed

- Calculation, Region and Region Size: a new module starts with **no data
  files selected**; choose the files explicitly instead of the module
  defaulting to every imported file.
- **Add module**: **Region** now sits in the **Gate & select** group,
  directly after **Gating** (it is a spatial gate producer).
- Figure filters and image overlays, Gate2Col and Heatmap now accept Regions
  from upstream Region modules alongside Gating gates.
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
