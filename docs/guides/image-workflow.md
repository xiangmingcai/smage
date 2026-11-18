# Spectral image workflow

Segmenting spectral microscopy images, aggregating per-object features and
comparing populations between samples.

## 1. Import the images

<div class="video-grid">
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/zzpiDxgE824" title="Import the images (part 1)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/gIVv938QoZY" title="Import the images (part 2)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
</div>

1. `File → New Project…`.
2. In **Data Import**, add your TIFF/OME-TIFF files.
3. Choose which channels to extract and name them (for example `DAPI`,
   `CD31`, `CD8`).
4. **Import**.

Every pixel becomes a row with its `x`, `y` and channel values.

## 2. Unmix and correct

<div class="video-frame">
  <iframe src="https://www.youtube-nocookie.com/embed/gJETmP1KBCE" title="Unmix and correct" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

Unmix and Illumination Correction prepare the channels that segmentation,
Regions and Feature Down read:

- Add **Unmix** when channels overlap spectrally; define the spectra and let
  Smage solve the per-pixel contributions.
- Add **Illumination Correction** when the field is unevenly lit; estimate the
  correction map with **Auto set** and save.

## 3. Segment

<div class="video-frame">
  <iframe src="https://www.youtube-nocookie.com/embed/UA55CXn2jAM" title="Segment" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

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

<div class="video-frame">
  <iframe src="https://www.youtube-nocookie.com/embed/hcajLI3rZJM" title="Detect Regions and measure their area" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

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

<div class="video-frame">
  <iframe src="https://www.youtube-nocookie.com/embed/RUqDa3ZN38o" title="Aggregate per object" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

1. Add **Feature Down**.
2. Set **Feature channel** to the mask column.
3. Add one aggregation rule per marker (for example `Mean` of `CD8`).
4. Check the **Background ignore** values so the background label is excluded.
5. **Calculate** — one row per segmented object is written per image.

The default rules table already contains the Count row and a group-key row
named after the feature channel; the group-key row can be deleted if you do
not want the identifier column in the output.

## 6. Gate populations

<div class="video-frame">
  <iframe src="https://www.youtube-nocookie.com/embed/ruhREiNVGj8" title="Gate populations" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
</div>

Add a **Gating** module below Segmentation (and Region) when you need to
define populations or exclude debris before aggregation:

- use image gates on a marker or the segmentation mask, or scatter /
  histogram gates on marker intensities;
- draw child and boolean gates as needed (NOT / AND / OR);
- gate membership becomes 0/1 columns that downstream modules can use.

Feature Down can aggregate a gate column as an extra rule (the mean per
object is the fraction of that object inside the gate), and Figure can use
the gate as a filter.

## 7. Compare and visualize

<div class="video-grid">
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/_uMRT2gWi3E" title="Compare and visualize (part 1)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/kdmgPvIxVbY" title="Compare and visualize (part 2)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/qk0PCVgEfcA" title="Compare and visualize (part 3)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
  <div class="video-frame">
    <iframe src="https://www.youtube-nocookie.com/embed/KkN88UF3ph8" title="Compare and visualize (part 4)" loading="lazy" allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
  </div>
</div>

- **Heatmap** for a clustered view of the per-class features.
- **Gating** on the aggregated table to define cell-type combinations.
- **Figure** to plot marker intensities per population.
- **Export** to write the feature tables.

## Notes

- Converted Regions are ordinary 0/1 channels downstream, so they can also be
  used as a Feature Down group key, gate input or Figure channel.
- Feature Up can map per-class values back onto individual objects for
  plotting.
