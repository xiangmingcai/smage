# Smage vX.Y.Z

## Highlights

- **Gating**: right-click the plot and choose **Save plot as…** to export the
  current view (gates and labels included) as an editable PDF, PNG or JPEG.
- **Feature Down**, **Feature Up** and **Gate2Col** calculate in the
  background, so the window stays responsive on large files.
- Workflow **Copy/Paste** keeps the saved **Data files** selection and maps it
  to the equivalent files in the new branch; the workflow view also keeps its
  scroll position when modules are added or deleted.
- Scatter **Fixed** point size is now a screen-pixel diameter, keeping points
  round when the X and Y axes have different scales.
- **Gating** keeps the Plot settings axes when switching files at the root;
  selecting a gate still uses that gate's axes.

## Downloads

- `Smage-vX.Y.Z-win64.zip` — portable Windows bundle (extract and run)
- `SHA256SUMS.txt` — checksum for the archive

## Installation

1. Download `Smage-vX.Y.Z-win64.zip` and `SHA256SUMS.txt`.
2. Extract the whole `Smage` folder to a local disk.
3. Double-click `Smage.exe`.

Windows 10 or 11, 64-bit. No Python installation is required.

## Verification

```bat
certutil -hashfile Smage-vX.Y.Z-win64.zip SHA256
```

Compare the result with `SHA256SUMS.txt`.

> **Code signing:** this build is not signed yet. Code signing will be added
> once the project has established enough adoption for an approved signing
> provider; the planned process is in
> [CODE_SIGNING_POLICY.md](https://github.com/xiangmingcai/smage/blob/main/CODE_SIGNING_POLICY.md).
> Verify the download with `SHA256SUMS.txt`.

## Notes

- Smage is research software, not a medical device, and must not be used for
  clinical decisions. See
  [DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md).
- Smage is released under GPL-3.0 with the approval of Amsterdam UMC. See
  [LICENSE](https://github.com/xiangmingcai/smage/blob/main/LICENSE) and
  [PRIVACY.md](https://github.com/xiangmingcai/smage/blob/main/PRIVACY.md).
- The full changelog is in
  [CHANGELOG.md](https://github.com/xiangmingcai/smage/blob/main/CHANGELOG.md).
