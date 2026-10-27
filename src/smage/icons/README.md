# Smage module icons

Monochrome icons shown in the workflow tree, the Add-Module dialog and the
module tabs. One file per task type, named after the `type` key in
`TASK_TYPES` (see `src/smage/main.py`).

This folder is the copy the app loads (`_module_icon_dir()` checks it first).
`data/icon/` holds the working copies and preview sheets.

## Rules

- 24 × 24 viewBox, ~1 px padding.
- Single ink colour `#39434F`; depth comes from `stroke-opacity` / `fill-opacity`
  (1.0 for structure, ~0.55 for secondary, ~0.10–0.45 for fills).
- Stroke width 1.7–2.0, `stroke-linecap` / `stroke-linejoin` round.
- No per-module colours; the shape has to carry the meaning.
- Must stay legible at 16 px.

## Files

| File | Module |
|---|---|
| import.svg | Data Import |
| export.svg | Export |
| gating.svg | Gating |
| figure.svg | Figure |
| scale.svg | Scale |
| rename.svg | Rename |
| unmix.svg | Unmix |
| segmentation.svg | Segmentation |
| compensation.svg | Compensation |
| illumination.svg | Illumination Correction |
| feature.svg | Feature Down |
| feature_up.svg | Feature Up |
| gate2col.svg | Gate2Col |
| subset.svg | Subset |
| subsample.svg | Subsample |
| dimred.svg | Dimension Reduction |
| cluster.svg | Cluster |
| area.svg | Area |
| data_clean.svg | Data Clean |
| batch_align.svg | Batch Alignment |
