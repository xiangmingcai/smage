# Figure

Creates publication-style plots from any point of the pipeline: scatter,
histogram and image plots, several per module.

![Figure](../assets/screenshots/figure.png)

## Settings

| Setting | Description |
|---|---|
| Plot type | Scatter, histogram or image. |
| File | The data file plotted by this figure. |
| Filter | Restrict the plotted events (for example to a gate). |
| X / Y parameter | The axes (Y is hidden for histograms). |
| Color mode | Density, a parameter value, or a solid color. |
| Color parameter / palette / point size | Appearance of the points. |
| Bins / display mode | Histogram binning and how bars are drawn. |
| X / Y range | Axis limits, in original data units. |
| Size (px) / resolution | Output size and DPI of the exported image. |
| Layers (image) | One layer per channel, with its own color, min/max and order. |
| Display toggles | Axes, axis names, tick indices and sample name. |

## Multiple plots

A Figure module can hold several plots; each has its own settings and its own
place in the export. Plots are listed in the module and can be removed
individually.

## Output

The plots are live views; use [Export](export.md) to write them as
PNG/SVG/PDF, or turn on the module's own export action.

## Notes

- Display transforms from an upstream [Scale](scale.md) module are applied
  automatically, and range fields are shown in the transformed units.
- Image plots can overlay a gate mask or a segmentation mask.

## Related

[Heatmap](heatmap.md) · [Export](export.md) · [Scale](scale.md)
