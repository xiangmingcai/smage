# Calculation

Builds **new channels** from existing ones with a list of ordered calculation
rows. It is a **map** module: the values are computed on demand for every
module below it and nothing is written to disk. The original data is never
modified.

A module can hold several rows, and a row may use the channel produced by a
row above it. This makes multi-step calculations possible (for example:
clip a channel, then scale it, then combine it with another channel).

Select the data files on the left before adding rows. A new Calculation
module starts with **no files selected**, so it never processes a file you
did not choose.

## Rows

| Column | Meaning |
|---|---|
| Type | Clip, Scale, Add or Contrast. |
| Files | The files this row applies to. Defaults to every file selected on the left; a subset is allowed. |
| Parameters | Channel inputs and the settings of the chosen type. |
| New channel | The name of the produced channel. Names are filled automatically as `channel_1`, `channel_2`, … and must not collide with an existing column or another row. |
| Downstream | When unticked, the row still feeds later rows in this module but its channel is hidden from downstream modules. |

Rows can be dragged by their handle. A row may reference only rows **above**
it; a move or deletion that would break a dependency is refused with an
explanation.

When a row uses a channel produced by another row, its **Files** must be a
subset of that row's files. This guarantees the referenced channel exists in
every file the row touches.

## Calculation types

### Clip

Forces every value below **Min** onto Min and every value above **Max** onto
Max. The thresholds are first filled from the source channel's data range, so
a new row starts as a no-op; edit them to clip. Minimum must be below
maximum; negative thresholds are allowed.

### Scale

Applies a real value transform (unlike the [Scale](scale.md) module, which
only changes how values are displayed):

- **Log** — `sign(x) · log10(|x|)`; zero stays zero, negatives stay negative.
- **Asinh** — `sign(x) · arcsinh(|x| / cofactor)`; cofactor defaults to 5.

### Add

A weighted sum of one or more channels: `Σ weight × channel`. The weight is 1
by default, may be negative or zero, and the same channel may appear more
than once. Terms can be added and removed (the delete button is disabled when
only one term remains).

### Contrast

An adjustable transfer curve. Click the plot to add an anchor, drag an anchor
to move it, right-click an interior anchor to remove it, and use **Reset to
identity** to start over. The curve is interpolated with a cubic spline and is
not required to be monotonic, so inverted curves are possible.

The **input range** (Min/Max) is filled from the channel's global range over
all files of the row. Editing it switches to manual; **Auto** and
**Recompute** restore automatic filling. Values outside the range are clamped
to its ends. The output uses the same units as the input.

## Preview

The right-hand panel shows the selected row's new channel as a scatter,
histogram or image plot. Preview settings (file, axes, color, ranges and
display toggles) work like the other plotting modules. Changing a row's
parameters updates the preview automatically, and image plots are available
only when the selected file is an image.

For an image preview, the axes are always the image's pixel coordinates
**x** and **y**. **Color channel** only chooses which channel is painted and
reported under the cursor; it never changes the axes.

The preview always reflects **unsaved** rows. Downstream modules only see the
rows after **Save**.

## Notes

- The channel lists of rows only include the variables shared by the files
  that row applies to.
- A row whose name conflicts with an existing column or another row is marked
  in red and cannot be saved until the name is changed.
- In an image plot zoomed far out, nonlinear rows (Clip, Contrast) are applied
  to the coarse image blocks for display; the stored values and statistics use
  the exact pixel data.

## Related

[Scale](scale.md) · [Rename](rename.md) · [Figure](figure.md) · [Gating](gating.md)
