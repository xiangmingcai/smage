# Illumination Correction

Corrects uneven illumination across an image by tiling the field of view,
estimating a per-tile brightness profile and dividing it out.

![Illumination Correction](../assets/screenshots/illumination.png)

## Settings

The panel is split into **Tiles calculation** and **Correction parameters**.

| Setting | Description |
|---|---|
| Channel | The channel to correct. |
| Tile W / Tile H | Size of the tiles the image is divided into. |
| Bin | Binning used when building the brightness profile. |
| Resolution | Resolution of the estimated correction map. |
| Files for illumination pattern recognition | Which files contribute to the estimate. |
| Correction method | How tile factors are combined into the correction map. |
| Correction intensity | Strength of the correction; 0 = none, 1 = full (default), 2 = double. |

**Tiles calculation** recognizes the illumination pattern. **Correction
parameters** apply it: the method picks the reference level (darkest,
brightest or median tile) and the intensity blends the resulting factor
linearly:

```
factor = T / mask
factor = 1 + intensity × (factor − 1)
```

Edits to the correction method or intensity are applied with the **Update**
button; the line below it shows the method and intensity currently in use.
Downstream channels use the saved settings, so they change only after the
module is saved.

**Auto set** estimates the tile size and background threshold from the data.
The preview plot shows the corrected channel, with its own display range and
palette. In **Plot settings**, the image levels (Min/Max, Auto set, Reset) are
shared by both previews; the **Before** histogram above them is read-only and
shows the uncorrected distribution. Zooming or panning re-samples the image at
the current magnification, so detail stays sharp when zoomed in. The button
below the previews copies the zoom and position of the preview under the
pointer to the other one; its arrow (**Sync view →** or **Sync view ←**) shows
the direction that will be used.

The channel can be an imported channel, a channel produced by an upstream map
module, or a generated **Unmix** channel. The tile calculation reads a
generated channel from its saved output. The tile width and height must fit
inside the image: an image smaller than one tile produces no data to estimate
the pattern from, and the panel shows a hint in that case.

**Recognize Illumination Pattern** reads a spread sample of each selected file
(up to about two million pixels, taken from batches across the file) instead
of loading the whole file, so large files are processed in seconds with
bounded memory. The progress line next to the button shows the current file,
and the button turns into **Cancel** while the calculation runs; cancelling
keeps the previous pattern. Each file's time is recorded in the run log.

The **Before** and **After** image previews always share one display range, so
a change of correction method, intensity or pattern is visible instead of
being normalized away by two independent automatic ranges. The range comes
from the sampled signal distributions; the Min/Max fields override it.

## Output

Corrected values for the selected channel. Downstream modules see the
corrected channel; the raw data is unchanged.

## Notes

- The correction map is estimated once per channel and cached; changing a
  downstream module does not re-estimate it.
- The signal histograms in **Plot settings** are read in the background from
  a sample of the channel, so switching file, channel or parameters never
  blocks the window. **Auto set** and **Reset** use the same sample.
- The illumination pattern used for estimation is persisted under
  `Module/illumination-<id>/` inside the project.
- Duplicating a submodule also copies its recognized illumination pattern
  (and how it was computed), so the copy starts from the same profile.
- Correction method and intensity edits apply to the preview when **Update**
  is clicked; the downstream channel changes when the module is saved.
  Unsaved edits are discarded when the module is closed.

## Related

[Unmix](unmix.md) · [Segmentation](segmentation.md) · [Scale](scale.md)
