# Segmentation

Segments nuclei (and optionally whole cells) from an image channel and writes
a mask column that downstream modules can use as the feature channel.

![Segmentation](../assets/screenshots/segmentation.png)

## Methods

| Method | Runs where | Description |
|---|---|---|
| Nuclear Expansion | Inside Smage | Classical threshold → components → size filter → optional split → expansion. |
| StarDist | External Python environment | Star-convex nucleus segmentation; installed from the Extension Manager. |
| Cellpose GPU | External Python environment | Learned segmentation on an NVIDIA GPU; installed from the Extension Manager. |
| Cellpose CPU | External Python environment | Learned segmentation on the CPU. Much slower than the GPU variant — use it only when no NVIDIA GPU is available. |
| Mask Input | Inside Smage | Import precomputed mask TIFF files. |

### Nuclear Expansion algorithm

1. Rasterize the nucleus channel into a 2-D image. Values pass through the
   map pipeline, so unmixed, compensated or illumination-corrected channels
   can be segmented directly.
2. Threshold the image: **Otsu** (default, computed from the histogram) or a
   **manual** value.
3. Remove single-pixel noise with a 3×3 opening, label 4-connected
   components, and discard objects smaller than the **minimum nucleus area**.
4. Optional **Split touching nuclei**: seeds are the local maxima of the
   Euclidean distance transform. Two seeds must be at least the **split
   minimum separation** apart (Auto = `sqrt(min_area / π)`); every foreground
   pixel takes the label of its nearest seed (a constrained Voronoi
   partition). Objects with a single seed are left unchanged.
5. Optional **Expansion radius**: every background pixel within that distance
   of a nucleus takes the nearest nucleus's label. Expansions never overlap —
   where two nuclei meet, the boundary is the equidistant line.

## Settings

| Setting | Description |
|---|---|
| Method | Nuclear Expansion, Cellpose or StarDist. |
| Nucleus channel | The image channel that contains the nuclei. |
| Threshold | Intensity threshold for the classical method. |
| Minimum nucleus area (px) | Smaller objects are discarded. |
| Split touching nuclei | Off by default. Separates nuclei that touch by seeding the distance-map maxima (see above). |
| Split minimum separation | Distance between two nucleus centres before they count as separate objects. `Auto` (0) uses the radius of a minimum-area nucleus; increase it to split less, decrease it to split more. |
| Expansion radius (px) | Grows each nucleus into the surrounding cytoplasm. |
| Mask column | Name of the column that receives the object labels. |
| Files to calculate | The image files to segment. |

The preview shows the mask over the image with configurable mask display,
colors and opacity.

## Calculation log

The dark log box below **Calculate** reports progress file by file — the
channel being rasterized, each nucleus count or error, and a closing summary.
The log is saved with the module: it is restored when the module is reopened,
and running the calculation again appends a new run to the history instead of
clearing it.

Calculations run in the background. Nuclear Expansion computes masks on a
worker thread, and Cellpose / StarDist prepare their images on a worker thread
before running in a separate Python process, so the window stays responsive
and other modules can be used while masks are being produced.

## Output

One mask per file, written under `Module/segmentation-<id>/` and exposed to
downstream modules as a label column (each object has a unique integer).

## Notes

- The mask column is a valid **feature channel** for
  [Feature Down](feature-down.md) and can be used for gating, plotting and
  export.
- Cellpose and StarDist run in the separate environment created by the
  Extension Manager; their models are downloaded on first use.
- Recalculating replaces the saved masks for the selected files.

## Related

[Feature Down](feature-down.md) · [Area](area.md) · [Figure](figure.md)
