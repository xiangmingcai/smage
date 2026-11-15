# Image workflow

Segmenting microscopy images, aggregating per-object features and comparing
populations between samples.

## 1. Import the images

1. `File → New Project…`.
2. In **Data Import**, add your TIFF/OME-TIFF files.
3. Choose which channels to extract and name them (for example `DAPI`,
   `CD31`, `CD8`).
4. **Import**.

Every pixel becomes a row with its `x`, `y` and channel values.

## 2. Optional: unmix and correct

- Add **Unmix** when channels overlap spectrally; define the spectra and let
  Smage solve the per-pixel contributions.
- Add **Illumination Correction** when the field is unevenly lit; estimate the
  correction map with **Auto set** and save.

## 3. Segment

1. Add a **Segmentation** module.
2. Choose the method: **Nuclear Expansion** (built in), **StarDist**,
   **Cellpose GPU/CPU** (Extension Manager) or **Mask Input** for precomputed
   masks.
3. Select the **nucleus channel**, set the threshold and minimum nucleus
   area, and enable **Split touching nuclei** if clumps need separating.
4. **Calculate** the mask. The mask column becomes available downstream.

![Segmentation](../assets/screenshots/segmentation.png)

!!! note
    Cellpose and StarDist run in the separate Python environment created by the
    **Extension Manager**. The first run downloads the model.

## 4. Optional: detect Regions and measure their area

1. Add a **Region** module and select the image files (nothing is selected
   by default).
2. Detect Regions with **Signal Expansion**, or draw them manually with the
   **Region in** / **Region out** tools.
3. In the Region tree, press **Convert as channel** for each Region you need
   downstream, then tick **Downstream visible**.
4. Add **Region Size**, select the converted Region channels and press
   **Calculate** for a file × Region pixel-area table.

See [Region](../modules/region.md) and [Region Size](../modules/region-size.md)
for the detection parameters, per-file thresholds, boundary editing and
multi-selection.

## 5. Aggregate per object

1. Add **Feature Down**.
2. Set **Feature channel** to the mask column.
3. Add one aggregation rule per marker (for example `Mean` of `CD8`).
4. Check the **Background ignore** values so the background label is excluded.
5. **Calculate** — one row per segmented object is written per image.

The default rules table already contains the Count row and a group-key row
named after the feature channel; the group-key row can be deleted if you do
not want the identifier column in the output.

## 6. Compare and visualise

- **Heatmap** for a clustered view of the per-class features.
- **Gating** on the aggregated table to define cell-type combinations.
- **Figure** to plot marker intensities per population.
- **Export** to write the feature tables.

## Notes

- Converted Regions are ordinary 0/1 channels downstream, so they can also be
  used as a Feature Down group key, gate input or Figure channel.
- Feature Up can map per-class values back onto individual objects for
  plotting.
