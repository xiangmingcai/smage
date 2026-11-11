# Illumination Correction

Corrects uneven illumination across an image by tiling the field of view,
estimating a per-tile brightness profile and dividing it out.

![Illumination Correction](../assets/screenshots/illumination.png)

## Settings

| Setting | Description |
|---|---|
| Channel | The channel to correct. |
| Tile W / Tile H | Size of the tiles the image is divided into. |
| Bin | Binning used when building the brightness profile. |
| Resolution | Resolution of the estimated correction map. |
| Correction method | How tile factors are combined into the correction map. |
| Files for illumination pattern recognition | Which files contribute to the estimate (and which files the correction is applied to). |

**Auto set** estimates the tile size and background threshold from the data.
The preview plot shows the corrected channel, with its own display range and
palette.

## Output

Corrected values for the selected channel. Downstream modules see the
corrected channel; the raw data is unchanged.

## Notes

- The correction map is estimated once per channel and cached; changing a
  downstream module does not re-estimate it.
- The illumination pattern used for estimation is persisted under
  `Module/illumination-<id>/` inside the project.

## Related

[Unmix](unmix.md) · [Segmentation](segmentation.md) · [Scale](scale.md)
