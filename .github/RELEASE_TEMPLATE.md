# Smage vX.Y.Z

## Highlights

- **Portable projects**: paths inside a project are stored relative, so the
  whole `.smage` folder can be copied to another folder or computer and opened
  directly. Existing projects are migrated on first open.
- **Rename Project…** in the File menu renames the project folder and project
  name together.
- **Save Project As** copies the complete project, including `Module/`.
- **Unmix** is now a generate module: **Run unmix** writes companion unmixed
  columns, and downstream modules read those generated outputs.
- **Segmentation** batches use a bounded background pipeline with **Cancel**,
  and keep disposable files under the project `tmp/` folder.
- **Figure** uses per-plot sampled pools and no longer retains full-file
  Compensation arrays, reducing memory use when switching large files.

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
