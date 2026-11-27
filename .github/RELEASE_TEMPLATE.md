# Smage vX.Y.Z

## Highlights

- **Illumination Correction** handles background-subtracted channels:
  **Shift** adds a constant to the channel at the submodule's input (the
  pattern estimate, the Before preview and the correction all work on the
  shifted values) and removes it again downstream, so a negative background
  is no longer amplified by the correction. **Pattern floor** raises a
  pattern that contains negative values, and **Paste illumination pattern** /
  **Copy pattern** move a pattern in and out through the clipboard.
- **Illumination Correction** responds immediately: **Update** and a
  re-recognized pattern re-apply the correction to the cached pixels of the
  current view, zooming redraws only the plot being zoomed, and raw-pixel
  previews are binned to the screen resolution. The Before/After histograms
  follow the Min/Max window, and **Correction intensity** accepts negative
  values.

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
