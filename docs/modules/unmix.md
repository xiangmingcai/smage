# Unmix — Spectrum Extraction

Separates overlapping fluorescence channels in image data (spectral
unmixing). The module has two tabs:

- **Spectrum Extraction** — build and inspect the reference spectra (this
  page).
- **[Matrix Unmixing](unmix-matrix.md)** — define signatures, check the
  unmixing matrix and run the unmixing.

![Unmix — Spectrum Extraction](../assets/screenshots/unmix.png)

## Spectrum settings

| Setting | Description |
|---|---|
| Statistic | How the reference spectrum is summarised per parameter (median is the default). |
| Distribution colormap | Colormap used when showing the per-parameter spread. |
| Parameters | The measured channels that make up a spectrum (16 in the example above). |
| Spectrum line color | Colour of the line in the spectrum plot. |
| Matrix decimal places | Number of decimals shown in matrix views. |

## Spectra

The middle list holds one entry per fluorophore plus its negative control
(`DAPI`, `DAPI neg`, `VIM`, `VIM neg`, …). Selecting one draws its spectrum on
the right: intensity across the measured parameters.

**New gate tree** adds an embedded gate tree below the plot. Gate the
single-stain image (for example to exclude debris or background) before the
spectrum is extracted — only the selected events contribute to the reference.

## Gates and image preview

The gate tree, tools and image plot work exactly like the
[Gating](gating.md) module, including the **Update statistics with full data**
button. The gate shape is stored per file.

## Output

The spectra and their gates feed the second tab. After running the unmixing
(see [Matrix Unmixing](unmix-matrix.md)), the module exposes one unmixed
channel per signature to downstream modules.

## Notes

- Unmixing is the most expensive stage; the matrix is cached per file and
  task, and files outside the module's sample list are skipped.
- Negative controls are subtracted per signature in the Matrix Unmixing tab;
  leaving them empty means no autofluorescence subtraction.
