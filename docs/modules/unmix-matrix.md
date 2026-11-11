# Unmix — Matrix Unmixing

The second tab of the [Unmix](unmix.md) module: it turns spectra into
signatures, checks how separable they are, and runs the unmixing on the
selected images.

![Unmix — Matrix Unmixing](../assets/screenshots/unmix-matrix.png)

## Signatures

| Column | Description |
|---|---|
| Positive spectrum | The spectrum of the fluorophore, taken from its single-stain control. |
| Negative spectrum | The spectrum of the negative control, subtracted as autofluorescence. |
| Normalize | Normalise the signature to its maximum (on by default). |
| Name | The fluorophore name; this becomes the output channel name. |

**Add signature** appends a row. The list should contain one signature per
fluorophore plus any autofluorescence signatures (`AF_tissue`, `AF_RBC`).

## Matrix views

| Tab | Shows |
|---|---|
| Signature matrix | Signature × parameter matrix as a heatmap with the values in each cell. |
| Signature curves | The signatures drawn as curves across the parameters. |
| Similarity matrix | Cosine similarity between signatures, fixed to the −1…1 scale. |

The similarity matrix is the quickest check: two very similar fluorophores
(bright cells in the off-diagonal) are hard to unmix and may need a different
panel or gating.

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
