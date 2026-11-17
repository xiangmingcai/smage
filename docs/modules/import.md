# Data Import

Turns raw files into the project's normalized table. Every downstream module
reads this table (or files generated from it).

![Data Import](../assets/screenshots/import.png)

## Supported input

- **Flow cytometry** — `.fcs` files.
- **Images** — TIFF / OME-TIFF stacks; each channel becomes a column and each
  pixel (or segmented object) a row.
- **Tables** — delimited text files (CSV/TSV).

## Settings

| Setting | Description |
|---|---|
| Files | The files to import. Each file is imported once and listed under `data/`. |
| Original name / Imported name | The column name in the source file and the name it gets inside the project. |
| Extract | Tick the columns that should be kept. Unticked columns are not written. |
| Sampling | Optional per-file down-sampling applied while importing (for very large tables). Images are always imported in full; use **Image resize** instead. |
| Apply to all files | Copies the current parameter settings to every selected file. |

## Resizing images

When Peek recognises a TIFF image, the Parameter panel gains an **Image
resize** group. Resizing happens while importing; the source TIFF is never
modified.

| Control | Description |
|---|---|
| Resize on import | Off by default. The width/height fields are pre-filled with the original size. |
| Width / Height | Target pixel size. Smaller values reduce the stored table and speed up every downstream module; larger values upscale the image. |
| Width ratio / Height ratio | The same target expressed as a multiple of the original size, with two decimals (`1.00×` = original, `0.50×` = half). Editing a ratio updates its pixel value, and editing a pixel value updates its ratio. The range is `0.01`–`10.00`; the pixel value is what gets imported and saved. |
| Keep aspect ratio (Y follows X) | When ticked, Height is computed from Width so the proportions stay the same, and the Height field and Height ratio are read-only. |
| Method | **Smooth (anti-aliased)** for intensity images, or **Nearest neighbor** to keep exact source pixel values (labels, masks). |

The imported size is shown in the Processed list and in the file's tooltip.
Sampling is disabled for image files: dropping random pixel rows would punch
holes in the image.

Ratios are only a convenient way to type a size; they are not stored in the
project. Very large originals can need a ratio below `0.01` to reach the
8-pixel minimum, and images already near the pixel limit cannot reach `10×`;
use the pixel fields for those extremes.

A resize target is limited to **200 megapixels or the original pixel count,
whichever is larger**. This guard prevents an accidental upscale from
exhausting memory while still allowing an already larger image to be imported
at its own resolution.

## Output

One normalized Parquet file per imported file in `<project>.smage/data/`,
registered in `data/columns.json`. Downstream modules can select these files.

## Notes

- Renaming a column here changes its name for the whole project; use
  [Rename](rename.md) if you only want to change names for a branch of the
  workflow.
- Image channels are stored as numeric columns (pixel intensities); image
  coordinates are kept as `x` and `y`.

## Related

[Rename](rename.md) · [Subset](subset.md) · [Segmentation](segmentation.md)
