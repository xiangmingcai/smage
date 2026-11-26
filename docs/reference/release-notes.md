# Release notes

The authoritative changelog lives in
[CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).

## 0.5.3 — 2026-11-26

- **Illumination Correction** is split into **Tiles calculation** and
  **Correction parameters**, with a new **Correction intensity** (0–100,
  default 1). Duplicating a submodule keeps its recognized pattern, Plot
  settings show read-only **Before** and **After** histograms, and
  correction-parameter edits reach downstream channels only after **Save**.
- **Feature Down**: **Background ignore** sits under **Data files** and can
  count from a chosen subset of files. The **Data files** table has a
  checkbox column for choosing what **Calculate** processes, and the default
  background value is counted off the UI thread.

See CHANGELOG.md for the complete list.

## 0.5.2 — 2026-11-26

- **Illumination Correction** pattern recognition accepts generated **Unmix**
  channels; the tile calculation reads them from their saved output.
- A tile size larger than the image now gets a clear message in the calculation
  result and a hint in the panel, instead of "No tile data was found".

See CHANGELOG.md for the complete list.

## 0.5.1 — 2026-11-26

- **Export — Image Export** lists each file's **Highest dtype** and an
  **Est. size**, with a **Know more about data type** reference. A **Data
  type** setting writes each file's own highest dtype or one dtype for the
  whole batch; the log lists every channel's source and export dtype. Values
  are transferred unchanged.
- **Segmentation** has a checkbox in the **Expose** column header that checks
  or clears every file at once.
- Module logs keep up to 10,000 lines and are stored as they are written, so
  they survive saving and reopening; **Batch Align**, **Data Clean** and
  **Export** now persist their logs automatically.
- **Fixed:** Unmix outputs created before 0.4.3 are visible to downstream
  modules again.

See CHANGELOG.md for the complete list.

## 0.4.4 — 2026-11-25

- **Unmix** is much faster on large image files. Output dtypes that depend on
  the results are decided by a scan that writes nothing, so the full-size
  float64 temporary file is gone; chunks are read and written with Arrow
  instead of pandas; and the image pyramid is built from the chunks being
  written instead of reading the new parquet back.
- **Unmix** shows progress in the status bar, logs each file's size, output
  dtype and elapsed time, and has a **Cancel** button that stops the run
  after the current chunk and deletes the partial output.

See CHANGELOG.md for the complete list.

## 0.4.3 — 2026-11-25

- **Unmix** has a **Channel data type** setting: **Keep type (float if
  negative)** (default), **Keep data type**, and **Adapt to results**. It
  controls whether generated parquet channels keep the source dtype, switch
  to a fitting float when negatives are present, or use the smallest dtype
  that can store each file's results.

See CHANGELOG.md for the complete list.

## 0.4.2 — 2026-11-24

- **Unmix**: **Copy matrix to clipboard** now writes 17 significant digits,
  enough to round-trip every float64 value. A matrix copied and pasted into
  another Unmix module reproduces the source values exactly, so unmixed
  results stay identical.

See CHANGELOG.md for the complete list.

## 0.4.1 — 2026-11-24

- **Portable projects**: project-internal paths are stored relative to the
  `.smage` folder, so the whole folder can be copied to another folder or
  computer and opened directly. Existing projects migrate on first open.
- **Rename Project…** in the File menu renames the project folder and project
  name together; it is available only while a project is open.
- **Save Project As** copies the complete project, including `Module/`, instead
  of only the data folder.
- **Unmix** is now a pure generate module: **Run unmix** writes companion
  unmixed columns, and downstream modules read those generated outputs.
- **Segmentation** batches run through a bounded background pipeline with
  **Cancel**, and disposable files live under the project `tmp/` folder.
- **Figure** uses per-plot sampled pools and no longer retains full-file
  Compensation arrays, reducing memory use when switching large files.

See CHANGELOG.md for the complete list.

## 0.3.4 — 2026-11-23

- **Figure**: multi-selected image plots now share a **Channels** table with
  Show / Parameter / Color / Min / Max / Name, Add/Remove, a channel histogram
  with Auto set/Reset, Show legend, an image Y range, and Resolution / Origin /
  Square controls.
- **Unmix**: after deselecting files that were already unmixed, **Unmix for
  image** asks for confirmation before deleting their saved outputs and
  removing the channels from downstream image plots.
- **Figure image plots** reload a generated image pyramid when an upstream
  module rewrites the same output file, so a renamed Gate2Col channel is shown
  as its 0/1 mask instead of falling back to the x gradient.
- **From scale** remains selected when the current file has no Scale and uses
  the data extent until one is available.

See CHANGELOG.md for the complete list.

## 0.3.3 — 2026-11-21

- **Gating** plots can be saved from the right-click menu as an editable PDF,
  PNG or JPEG, with gates and labels included.
- **Feature Down**, **Feature Up** and **Gate2Col** calculate in the background,
  so the window stays responsive; progress appears in the status line and log.
- Workflow **Copy/Paste** keeps the saved **Data files** selection and remaps it
  to the equivalent files in the new branch. The workflow view also keeps its
  scroll position when modules are added or deleted.
- Scatter **Fixed** point size is now a screen-pixel diameter, so points stay
  round when the X and Y axes have different scales.
- **Gating** keeps the Plot settings axes when switching files at the root;
  selecting a gate still uses that gate's axes.

See CHANGELOG.md for the complete list.

## 0.3.2 — 2026-11-19

- **Extensions**: **Install dependencies** now upgrades pip in the target
  environment to at least 23.2 before installing Cellpose/StarDist packages.
  This fixes the `typing_extensions` / `flit_core` failure with the old pip
  bundled in some Python 3.11 venvs.

See CHANGELOG.md for the complete list.

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
