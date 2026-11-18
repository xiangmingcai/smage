# Smage vX.Y.Z

## Highlights

- **Subsample** can sample inside gates shared by every selected file, with an
  **In gates** column and gate-based percentages and allocation.
- **Data Import** image resize adds linked width/height ratio fields and allows
  up to 200 megapixels or the original image size.
- **Figure**, **Gating** and **Calculation** axes offer **Manual**,
  **From scale** and **Auto** ranges, remembered per channel, with a 5 %
  border around the data.
- **Gating** waits for a Data files selection, and the **Scale** preview only
  offers the selected files.
- Light, readable tooltips in every settings panel and colour swatch.

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
