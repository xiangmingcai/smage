# Multiplex image workflow

Analyzing high-plex tissue images — imaging mass cytometry, MIBI, CODEX or
cyclic immunofluorescence — from a multi-channel image stack to per-cell
marker tables and population comparisons.

## 1. Import the image stack

1. `File → New Project…`.
2. In **Data Import**, add the OME-TIFF or multi-page TIFF containing all
   marker channels.
3. Keep every marker channel and rename it to the marker name (for example
   `DAPI`, `CD31`, `CD8`, `PD1`).
4. **Import**.

Each pixel becomes a row with its `x`, `y` and the intensity of every
channel. Large panels make the imported table much wider than a
single-marker image; if the source resolution is higher than the analysis
needs, enable **Image resize** in the Parameter panel before importing.

## 2. Correct the channels

These steps are optional and depend on the panel and the instrument:

- **Unmix** when channels overlap spectrally; build the signatures and unmix
  for image.
- **Illumination Correction** when the field is unevenly lit.
- **Batch Align** to align cohorts or staining batches before comparing
  samples.
- **Scale** only changes how values are drawn; it never changes the stored
  intensities.

A typical high-plex pipeline applies Unmix below Data Import and keeps the
resulting marker channels as the input for segmentation and feature
aggregation.

## 3. Calculate channels for segmentation

Use the **Calculation** module to build an input channel that is easier to
segment than any raw channel, for example:

- combine several membrane markers with **Add** into one membrane channel;
- use **Scale** (Log/Asinh) or **Clip** to compress bright background and
  expand the nucleus signal;
- use **Contrast** to reshape the intensity histogram before thresholding.

Calculation is a map module: rows are evaluated on demand, nothing is written
to disk, and the new channels are available to Segmentation, Region, Feature
Down and Figure. A row can be limited to a subset of files and hidden from
downstream modules while still feeding later rows.

## 4. Segment nuclei and cells

1. Add a **Segmentation** module.
2. Choose **Nuclear Expansion**, **StarDist**, **Cellpose GPU/CPU** or
   **Mask Input**.
3. Use the DNA/nucleus channel (for example `DAPI`) and set the threshold,
   minimum area and expansion radius.
4. Enable **Split touching nuclei** when the tissue is dense.
5. **Calculate** the mask.

The mask is a label channel: every segmented object has a unique integer.
Downstream modules use it as the **Feature channel** (the object identifier).

## 5. Optional: tissue Regions

For compartments such as tumour, stroma or perivascular space, add a
**Region** module:

1. Detect Regions with **Signal Expansion**, or draw them manually with the
   **Region in** / **Region out** tools.
2. Press **Convert as channel** for each Region and tick **Downstream
   visible**.
3. Use the converted 0/1 channel as an extra Feature Down rule (its mean is
   the fraction of an object inside the Region) or as a Figure layer.

Add **Region Size** when the pixel area of a compartment per file is needed.

## 6. Aggregate marker intensities per object

1. Add **Feature Down** and set **Feature channel** to the mask column.
2. Add one rule per marker (for example `Mean` of `CD8`, `Median` of `PD1`).
3. Check **Background ignore** so the background label is excluded.
4. **Calculate** — one row per object, with one column per marker.

The feature table is the input for clustering, dimension reduction and
heatmaps.

## 7. Gate populations

After Feature Down, the table has one row per object. Add a **Gating** module
to define cell types on marker intensities:

1. Choose two marker columns for a scatter gate (or one for a histogram).
2. Draw parent gates and child gates; a selected gate creates the next
   level.
3. Combine gates with **NOT / AND / OR** for negative or multi-marker
   populations.
4. Counts and percentages are now per object, because the aggregated table
   has one row per cell.

Gate membership becomes 0/1 columns for Figure filters, Gate2Col, downstream
aggregation and export.

## 8. Explore and compare

- **Cluster** to group cells by marker expression.
- **Dimensionality Reduction** (UMAP or t-SNE) to embed the same features.
- **Heatmap** for a clustered marker × population view.
- **Figure** to compare marker intensities or cluster frequencies between
  samples.
- **Feature Up** to map per-cluster values back to individual objects when a
  spatial or per-cell plot is needed.

Use **Subsample** above the clustering modules when a file has millions of
pixels or objects.

## 9. Export

Add an **Export** module to write the feature tables (CSV/parquet) or the
figures. The project folder keeps every module setting, so the analysis can
be reopened and reproduced.

## Notes

- Keep channel names stable across files; Feature Down and Batch Align match
  columns by name.
- On large images, calculations use the image pyramid and still take time;
  close unused module tabs to release cached data.
- Converted Regions are ordinary 0/1 channels, so they can be used anywhere a
  channel is accepted.
- Smage is research software and not for clinical decisions; see
  [DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md).
