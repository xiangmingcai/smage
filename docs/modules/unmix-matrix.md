# Unmix — Matrix Unmixing

The second tab of the [Unmix](unmix.md) module: it turns spectra into
signatures, checks how separable they are, and runs the unmixing on the
selected images.

![Unmix — Matrix Unmixing](../assets/screenshots/unmix-matrix.png)

## Signatures

The **Signature source** at the top of the panel chooses where the matrix
comes from. Both sets of data are kept when you switch, so nothing has to be
rebuilt.

### From spectra (default)

| Column | Description |
|---|---|
| Positive spectrum | The spectrum of the fluorophore, taken from its single-stain control. |
| Negative spectrum | The spectrum of the negative control, subtracted as autofluorescence. |
| Normalize | Normalise the signature to its maximum (on by default). |
| Name | The fluorophore name; this becomes the output channel name. |

**Add signature** appends a row. The list should contain one signature per
fluorophore plus any autofluorescence signatures (`AF_tissue`, `AF_RBC`).

### Manual input

Choose **Manual input** to use a matrix that was built elsewhere. **Paste
matrix** reads the clipboard; the expected text is exactly what **Copy
matrix** writes:

```text
Signature	CD3	CD8	CD45
Signature 1	1.0	0.4	0.2
AF_tissue	0.1	0.9	0.3
```

The first header cell must be `Signature`. The channel set must match the
channels selected in **Spectrum Extraction**; a different order is accepted
and kept as pasted. Rows with a missing name, duplicate names, a wrong column
count, an empty cell or a non-numeric value are rejected with a message and
the current matrix is left untouched.

Every pasted cell can be edited, and the rows can be dragged into a new order
by their header. Columns start compact (the Signature column is 20 characters
wide, value columns 5) and any divider can be dragged to widen them; long
channel names are elided until you do. If the channel selection changes after pasting, the matrix
is kept but marked with a warning, and **Unmix for image** refuses to run
until the paste matches the selection again (a channel reorder is fine).

## Matrix views

| Tab | Shows |
|---|---|
| Signature matrix | Signature × parameter matrix as a heatmap with the values in each cell. |
| Signature curves | The signatures drawn as curves across the parameters. |
| Similarity matrix | Cosine similarity between signatures, fixed to the −1…1 scale. |

The similarity matrix is the quickest check: two very similar fluorophores
(bright cells in the off-diagonal) are hard to unmix and may need a different
panel or gating.

Right-click a matrix heatmap and choose **Save as…** to write it as it is
currently shown (zoom included). **PDF** keeps the cells, numbers and names
as vector shapes and editable text; **PNG/JPEG** are written at three times
the screen resolution. Right-clicking the **Signature curves** plot offers
the same formats; its PDF keeps the axes and text editable, and the
signature legend above the plot is included in the export.

**Similarity settings** control the rendering: cell width/height, colormap,
show numbers, auto number colour, number colour, and **Copy similarity to
clipboard**.

## Samples to be unmixed

Click **Select files…** to choose the images that should be unmixed in the
shared multi-select dialog. Only these files get unmixed channels downstream.

## Running

1. Choose the **Unmixing method** (`OLS` — ordinary least squares — is the
   default).
2. Press **Unmix for image**. The log shows progress per file and a summary
   (`generated`, `already up to date`, `failed`, `skipped`).
3. Save the module so downstream modules see the unmixed channels.

## Notes

- A signature with an empty positive spectrum is ignored; the log warns when
  a file has no usable image data.
- Unmixed outputs are cached per file; changing a signature invalidates only
  the affected outputs.
- The unmixed channels are ordinary columns, so they can be plotted, gated,
  segmented or aggregated like any other channel.
