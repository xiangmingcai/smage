# Packaging

The Windows build is a PyInstaller **onedir** bundle; the release artifact is
the zipped `dist\Smage` folder.

## Build

```bat
.venv\Scripts\python.exe packaging\build_win.py
```

The script:

1. runs PyInstaller with `packaging/smage.spec`;
2. runs `Smage.exe --self-test` (imports every bundled module, including the
   dynamic imports);
3. runs the bundled end-to-end regression suite inside the frozen application
   (`Smage.exe --run-tests --tag e2e`).

Any failure fails the build. Results are written to
`%APPDATA%\Smage\selfcheck.log` and `%TEMP%\SmageTests\frozen-latest.md`.

## What the bundle contains

- `Smage.exe` plus the Python runtime and every dependency under `_internal/`.
- Application icons and external runner scripts.
- The regression suite, so users and maintainers can verify the build.

## Release checklist

1. Update `__version__` in `src/smage/__init__.py`.
2. Run `run_all.py --full`.
3. Run `packaging/build_win.py`; both self-checks must pass.
4. Zip `dist\Smage` as `Smage-vX.Y.Z-win64.zip`, generate `SHA256SUMS.txt`.
5. Create a GitHub release with the zip, the checksums and the release notes.
6. Signing (planned): sign `Smage.exe` before zipping, and the installer
   afterwards; always with an RFC 3161 timestamp.

## Notes

- UPX is disabled in the spec; compressed executables trigger more antivirus
  false positives.
- The spec pins per-distribution metadata with `copy_metadata`, so
  `importlib.metadata` works inside the bundle.
- Per-module caches (Unmix matrices, illumination masks) are rebuilt on first
  use; nothing machine-specific is stored in the bundle.
