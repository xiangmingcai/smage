# Changelog

All notable changes to Smage are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [0.3.3] — 2026-11-21

### Added

- **Gating**: right-clicking the plot offers **Save plot as…**, using the same
  export as Figure: an editable PDF with vector axes and text (gates and
  labels are included), or a PNG/JPEG image.

### Changed

- The Workflow tree keeps its scroll position when a module is added, pasted
  or deleted instead of jumping back to the top.
- **Feature Down**, **Feature Up** and **Gate2Col** now run their Calculate /
  Convert work on a background thread and report progress through the status
  line and log. The window stays responsive while a large file is processed;
  closing the module still stops the run after the current file.
- Workflow **Copy/Paste** keeps the saved **Data files** selection and points
  it at the equivalent files of the new branch (matching the parquet filename,
  then the display name). Selections with no equivalent in the new branch are
  cleared. This covers Gating and Region stores and the preview file.
- **Gating**: switching files while no gate is selected (root) keeps the X/Y
  axes configured in Plot settings; a selected or active gate still uses its
  own axes.

### Fixed

- Scatter **point size** is now always a screen-pixel diameter. With
  **Fixed** checked it could previously be interpreted in data units, so a
  round point became an ellipse when the X and Y axes had different scales.
  Very large sizes saved by older projects fall back to the default.

## [0.3.2] — 2026-11-19

### Fixed

- **Extensions**: installing Cellpose/StarDist dependencies now upgrades pip
  in the target environment to at least 23.2 first. Older pip (for example
  the 22.3.1 bundled with Python 3.11 venvs) rejected the `typing_extensions`
  wheel and then failed to build it because the PyTorch index has no
  `flit_core`.

## [0.3.1] — 2026-11-19

### Added

- The spectral and multiplex image workflow guides now embed video
  walkthroughs for every step.
- README documents the optional Cellpose, StarDist, FlowSOM and PARC
  extensions with their versions, repositories and the Python 3.11 note.

### Fixed

- **Feature Up**: **Match** now pairs files by their visible names instead of
  the stored parquet filename. Imported files are stored as
  `data/<hash>.parquet`, so the old prefix test never matched; a message now
  appears when no automatic pairing is possible.
- **Feature Up**: **Save** now persists the settings and clears the unsaved
  changes flag (the button called a helper that was not registered on the
  context).
- **Feature Up** logs name the mapped output after the visible To file
  (`sample_mapped.parquet`) instead of the internal hash-based filename.

## [0.3.0] — 2026-11-18

### Added

- **Extensions**: the Python environment path field now shows example
  `python.exe` locations for conda environments and venvs.
- **Data Import** image resize offers linked **Width ratio** and **Height
  ratio** fields next to the pixel sizes. Ratios use two decimals
  (`0.01×`–`10.00×`) and stay in sync with the pixel values; pixels remain
  the stored and imported size.
- **Subsample** can sample inside one or more upstream gates. The new
  **Filter** setting lists only gates shared by every selected file; multiple
  gates are combined with OR. The table gains an **In gates** column, and
  percentages, Total allocation and manual count edits are based on the
  in-gate point count. Image coverage masks match the gate restriction.

### Changed

- **Scale**: the preview's **File** list now offers only the files chosen in
  **Data files**, so the preview always shows the module's changes. The first
  selected file opens automatically; deselecting the current file switches to
  the first remaining one, and clearing the selection returns the preview to
  an empty state with a **Select data files to start.** hint.
- **Gating**: a new module no longer draws the first available file. The plot
  stays empty with a **Select data files to start.** hint, and the gate tools
  and Col2Gate are disabled until files are chosen. Selecting files opens the
  first one with its first two channels as X and Y. The preview keeps the
  current file while it remains selected, and returns to the empty state if
  the selection is cleared.
- **Plots**: scatter, overlay and histogram views keep a 5 % border, so the
  active Min/Max sit at 5 % and 95 % of the visible axis instead of on the
  frame. This applies to every range mode and to the Gating, Calculation and
  other previews. Saved and displayed Min/Max are unchanged; image plots keep
  their exact pixel extent. Histogram count axes follow the same rule, so the
  bottom of the axis is slightly below zero.
- **Figure**: plot X/Y ranges now have **Manual**, **From scale** and **Auto**
  modes. From scale (the default) uses the nearest upstream Scale that sets
  both Min and Max, and falls back to Auto when no Scale does; Auto uses the
  drawn data extent and ignores Scale. Overlay plots now use Scale ranges
  too, and histogram count axes offer Manual/Auto. The Gating preview also
  defaults to From scale. X/Y ranges are remembered per channel (in Figure
  plots, Gating previews and Calculation previews), so switching channels
  restores each channel's mode and manual Min/Max; a channel seen for the
  first time starts From scale.
- **Data Import**: the image resize target limit is now 200 megapixels or the
  original pixel count, whichever is larger (previously 64 megapixels).
- **Subsample**: the **Sampling basis** label is now **Sampling method**.
- **Unmix**: the gate-tree action **Re-set as spectrum** is now **Set other
  gate as spectrum**.

### Fixed

- **Tooltips** inside transparent settings panels no longer fall back to the
  dark Windows tooltip. The containers' bare `background: transparent;` rules
  (Data Import's Parameter panel, Figure, Scale, Heatmap, Illumination,
  Region, Subsample, Unmix and Gating settings) are now scoped to the
  container with an object name, so they no longer override QToolTip's
  palette. Colour swatches with tooltips (the Gating **Gate color** button
  and Figure's **Colors differ** button) use the same scoped rule, so their
  tooltips stay light too. The light tooltip rule is kept in the shared
  `TOOLTIP_QSS` constant.
- **Figure multi-selection Manual ranges** are now entered in original units
  and converted through each channel's Scale transform. Previously a value
  such as 10000 was stored as a plot unit, which made arcsinh/log10 axes show
  `nan` (or an empty plot) when converted back.
- **Range mode tooltips** use a white background with dark text instead of the
  platform's dark tooltip palette.
- **Figure**: the image plot's **Channels** table now grows with the number of
  added channels instead of scrolling internally, so every channel stays
  visible; the settings panel scrolls instead.
- StarDist: model downloads now verify TLS with the Windows certificate store
  (`truststore`) or a CA bundle from `SMAGE_CA_BUNDLE`, instead of failing
  with `CERTIFICATE_VERIFY_FAILED` behind a corporate proxy. The StarDist
  extension also installs `certifi` and `truststore`.

## [0.2.0] — 2026-11-16

Second packaged release. Smage is now released under GPL-3.0 with the
approval of Amsterdam UMC, the Netherlands.

### Added

- **DISCLAIMER.md** at the repository root, plus a short statement in
  **Help ▸ About Smage** with a link to it: Smage is research software and
  not a medical device, must not be used for clinical decisions, comes
  without warranty, and the user is responsible for the analysis and its
  results. The document also states how AI-assisted development works and
  which human review, testing and accountability steps apply.
- **Region** (new map module): detects image regions and converts them to
  0/1 channels for downstream modules. It provides an internal detection-gate
  tree and **Signal Expansion** detection, a Region → Sample → Sub-region
  tree with per-item visibility, rename, delete and branch copy/paste, and
  per-Region fill/edge drawing styles.
  Converted Regions are ordinary channels in Figure, Heatmap, Gate2Col and
  Region Size. Internal detection gates support **boolean gates** (NOT /
  AND / OR) and **per-file geometry** (adjust a gate separately on each
  file, or copy one file's shape to all).
- **Region Size** (new module, replaces Area): calculates the pixel area of
  each selected Region in each selected image file as a file × Region table.
  Missing Samples are **NA**, empty Samples are **0**, and overlapping
  sub-regions are counted once. Rows and columns can be reordered and the
  table is copied with **Copy table**. A new module starts with no Regions
  selected; **Calculate** shows the table and **Save** stores it and locks
  the upstream modules. A log box under the table reports progress.
- **Calculation** (new map module): builds new channels from existing ones
  with an ordered list of calculation rows — **Clip**, real-value **Scale**
  (Log/Asinh), weighted **Add**, and a custom **Contrast** curve. A row may
  use a channel produced by a row above it, apply to a subset of the files,
  and be hidden from downstream modules while still feeding later rows. The
  module writes no data: saved rows become a map stage evaluated on demand,
  so every module below it sees the new channels; unsaved edits appear only
  in the live preview.
- Unmix: **Signature source** under Signatures can now be **From spectra**
  (unchanged) or **Manual input**. Manual input replaces the signature editor
  with **Paste matrix**, which accepts the TSV written by **Copy matrix** from
  another Unmix module, checks that the channels match the Spectrum
  Extraction selection, and shows the matrix as an editable table whose rows
  can be dragged into a new order. Columns start compact (20 characters for
  the Signature column, 5 for each value column) and every divider can be
  dragged to widen it. Both sources are saved with the module, so switching
  between them loses nothing. **Copy matrix** now writes one decimal.
- Unmix: the matrix heatmaps (**Signature matrix**, **Similarity matrix**) and
  the **Signature curves** / spectrum plots have a right-click **Save as…**
  menu. Heatmaps export as a PDF with vector cells and editable numbers and
  names, or as PNG/JPEG at three times the screen resolution; plots use the
  same editable-axes PDF / high-resolution image pair as Figure, and the
  signature legend above the curves is included.
- Heatmap: the colour legend now has a **Legend title**. It defaults to the
  Normalize mode (`Raw value`, `Row z-score`, `Column z-score`) and follows
  that setting until the title is edited; clearing the box hides it. The
  title is drawn rotated beside the color scale and is included in exports.
- Heatmap: right-click the heatmap and choose **Save as…** to write it as
  currently shown — a **PDF** with vector cells, grid, dendrograms, color
  scale and editable text, or a **PNG/JPEG** at three times the screen
  resolution.
- Feature Down: **Add batch** next to the rules table. It opens the shared
  multi-select dialog to pick several channels, then one dialog sets the
  Method, Range, Percentile and new-column name suffix for all of them.
  Names are `<channel>_<suffix>` with automatic `_1`, `_2` de-duplication.
- Feature Down: a new **Mode** aggregation rule returns the most frequent
  value in each class (after the range filter). Ties take the smallest
  value, so the result is deterministic. The rules panel explains Mode next
  to the existing Range note.
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
- Figure: an image plot filtered by a Region kept the filter only on normal
  image channels. Switching to an Unmix, Segmentation, Gate2Col, Feature Up,
  Dimension Reduction, Cluster or Data Clean channel rastered that channel
  from its own saved pyramid and never applied the filter mask, so the full
  image appeared. The shared image renderer now derives the mask coverage
  for those rasters too.
- Figure: applying a Region filter to an image is much faster. The filter
  worker no longer reads x/y for every pixel and tests each ring with ray
  casting; it rasterises the Region rings directly with Pillow. The UI
  thread also skips the x/y column load that was only used to validate a
  purely spatial Region filter, and the filter computation time is written
  to the project log (`image.filter`).
- Region: saving the Region tree now invalidates the cached copy, so
  downstream Region channel lists and Region Size options see the new names
  and conversions immediately instead of the previously loaded tree.
- Region detection: a detected component that touches the image border no
  longer loses its holes. contourpy clips such a mask into open polylines,
  which were all classified as `in` rings, so the even-odd fill showed the
  holes — the empty areas between cells — as Regions and left the real
  foreground unpainted. The mask is padded by one pixel before tracing, so
  the outer contour is closed and holes stay `out` rings.
- Region detection: every component is traced inside its own bounding box
  instead of re-tracing the whole image per component. A detection with
  2053 components spent 129–155 s in the contours stage; it now takes about
  one second.
- Gating: image gate subsets are rasterised at full pixel resolution, so
  block-mean pooling can no longer show pixels from inside a polygon in a
  NOT subset.
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

- **Heatmap gate labels**: a gate (row) name no longer carries its module name
  when the name is unique. The module is added only when several upstream
  Gating modules contain a gate with the same name, so those rows stay
  distinguishable without cluttering every label.
- **Gating subset view**: double-clicking a **NOT / AND / OR** gate shows only
  the gates drawn inside it. The gates the boolean gate combines belong to the
  level above and are no longer drawn over the subset, where the outline
  marked points that the boolean gate excludes.
- Region sub-regions now use the `in`/`out` ring representation. Projects
  created with the earlier keyhole-polygon Region format are not migrated.
- Calculation, Region, Region Size and Heatmap: a new module starts with
  **no data files selected**; choose the files explicitly instead of the
  module defaulting to every imported file.
- **Add module**: **Region** now sits in the **Gate & select** group,
  directly after **Gating** (it is a spatial gate producer).
- **Region layout**: seven independent panels — Data files, Detection gates
  and Regions on the left; image and Detect Region in the middle; Image
  settings, Region setting and Log on the right. The columns default to
  2:2:1, the left stack to 1:1:3, the middle to 3:1 and the right to 3:1:1;
  every divider is draggable. **Region drawing** is renamed **Region
  setting**. The Regions tree gives **Region : Show** a default 4:1 width.
- **Region in/out tools**: Sub-regions are now stored as `in` (filled) and
  `out` (hole) rings and evaluated with even-odd fill, so a Region can be
  carved into holes or split into several pieces. The image toolbar now has
  **Gating tools** (rect / ellipse / polygon) and **Region tools**
  (**Region in** / **Region out**); the Cancel button is replaced by Esc,
  clicking the active tool again, or switching tools. Drawing a Region in
  inside an existing hole creates an island without deleting the out ring.
  Anchors are re-traced through a pixel mask after each edit.
- **Region detection threshold**: the **Auto threshold** checkbox is now a
  **Threshold mode** choice — **Auto (Otsu)**, **Percentile** (each file's
  own percentile; 30% leaves about 70% of that file's pixels above the
  cut-off) or **Manual** (one fixed value). Auto and Percentile are computed
  separately for every file, and the threshold actually used is written to
  the log for each file, both on the threshold stage line and on the
  per-file result line. A warning is logged when the foreground covers 80%
  or more of the image, which points at a threshold below the background
  noise or an over-large **Expand**.
- **Region detection**: Signal Expansion now emits `in/out` region parts
  instead of keyhole polygons; Detect Region's spin
  boxes and combos ignore the mouse wheel so a scroll cannot change a
  parameter.
- **Region detection log**: the progress bar under Detect Region is gone;
  per-file progress and results are written to a log panel that uses the
  user-visible file names, is saved with the module and appends across
  runs. Every detection stage logs its own timestamp and elapsed seconds
  (read, gate, smooth, threshold, binarize, expand, fill, filter, contours,
  total) for profiling.
- **Region tree**: clicking a Sample or Sub-region Show checkbox now switches
  the image to that file. Every sample switch explicitly removes the previous
  overlay items, so Regions no longer accumulate on the image when browsing
  Samples (PlotItem.clear() does not remove arbitrary QGraphicsItems).
  Clicking a filled Sub-region directly in the image selects it in the tree.
- **Region tree**: a **Sample** node can now be copied as a branch and
  pasted into another Region, copying all of its Sub-regions with new ids.
  Pasting checks whether that Region already has the Sample and refuses with
  a message instead of creating a duplicate.
- **Region channels**: every Region row now has **Convert as channel**,
  **Status** and **Downstream visible** columns. Converting writes a
  0/1 `[x, y, mask]` parquet plus an index pyramid for every file that has a
  Sample, and the channel becomes available to downstream modules only when
  the visibility box is ticked. Region names are checked against every
  channel name in the data files selected by all modules (rename and
  convert). This is the **only** downstream representation: Regions are no
  longer exposed as gates or filters, so Figure filter options, Gate2Col,
  Heatmap annotations and Figure overlay gates no longer list them. Region
  Size now lists converted + visible Region channels and reads their pixel
  counts from the mask.
- **Region tree**: **Rename** is now available in the context menu for
  Region and Sub-region nodes (Sample still cannot be renamed); the inline
  editor can still be opened by double-clicking the name.
- **Region image**: the right-click menu is removed — pyqtgraph's built-in
  View All / Mouse Mode / Export actions are no longer offered, since the
  Region page has no image context actions yet.
- **Region tree multi-selection**: **Shift** selects a range and **Ctrl**
  toggles individual rows, so several Sub-regions can be selected at once.
  All selected Sub-regions on the displayed file are highlighted together,
  and **Copy Branch** / **Delete** apply to the whole selection. A copied
  group pastes as several Sub-regions with new ids.
- **Sub-region selection**: selecting a Sub-region in the tree or clicking it
  in the image now only highlights its boundary and selects it in the tree.
  The anchor handles are no longer rebuilt on every selection, which made
  complex boundaries freeze the window. **Edit boundary…** in the Sub-region
  context menu shows the anchors; when the boundary has more than 100
  anchors, Smage first warns with the count and asks for confirmation,
  because displaying that many handles can freeze or crash the app.
- **Region setting**: the edge Width spin box ignores the mouse wheel, and
  widths below 3 px are no longer clamped to 3 px when the Region is
  selected.
- **Detect Region panel**: changing the method or a parameter keeps the
  panel's scroll position instead of pulling it to the bottom. Every signal
  option now has a short explanation underneath it, using the same hint
  style as the other modules.
- **Region fill holes**: a new **Max hole area** setting fills only holes up
  to a given pixel area (0 keeps the old behaviour of filling every enclosed
  hole), so large lumens between cells can stay open. Region polygons also
  keep enclosed holes as `out` rings instead of covering them with the
  outer contour, so blank lumens are no longer painted as Regions.
- **Region image settings** now match the image options of the Gating pane:
  file navigation, channel and levels, display toggles (axes, names,
  indices, sample name) and the three font sizes.
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

- Code signing for the Windows executable and installer (provider to be selected).
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
