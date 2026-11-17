# Code Signing Policy

_Last updated: 2026-11-17_

Smage releases are currently **unsigned**. Code signing will be enabled once
the project has established enough adoption and a suitable signing provider
has been approved. This policy describes what will be signed, how it will be
built and who will approve a signing request.

## Project

- **Project:** Smage — spectral image and flow-cytometry analysis
- **Repository:** <https://github.com/xiangmingcai/smage>
- **License:** GNU General Public License, version 3 (GPL-3.0)
- **Maintainer:** Xiangming Cai, Amsterdam UMC
- **Advisor:** Juan J. Garcia Vallejo, Amsterdam UMC

## Scope

When signing is enabled, only official Windows binaries built from this
repository will be signed:

- `Smage.exe` and the files inside the portable bundle;
- a future `Setup.exe` installer, if one is published.

Binaries from forks, local builds, modified copies or third parties are
never signed with this project's certificate.

## Source and build system

- Signed artifacts are built by GitHub Actions on `windows-latest` using
  `.github/workflows/build-release.yml` from a tagged commit in this
  repository.
- The workflow installs dependencies from the committed
  `requirements-lock.txt`, runs PyInstaller, `Smage.exe --self-test` and the
  frozen end-to-end regression suite, and only then creates the release
  archive.
- No binary that was built outside this workflow is submitted for signing.

## Roles and approval

- **Author:** Xiangming Cai
- **Reviewer:** Xiangming Cai, with the advisor where applicable
- **Approver:** Xiangming Cai

Every signing request must be manually approved by the approver in the
chosen signing service before a certificate is applied. A release is never
signed automatically without that approval.

## Signing process

1. A version tag (`vX.Y.Z`) is pushed to the repository.
2. The GitHub Actions workflow builds and tests the portable bundle.
3. The workflow submits `Smage.exe` to the approved signing service.
4. The approver reviews and approves the request in that service.
5. The signed binary is returned to the workflow, packaged into
   `Smage-vX.Y.Z-win64.zip`, and published with `SHA256SUMS.txt` and the
   release notes.

The private key will be held by the selected certificate authority or
signing service in a hardware security module; Smage never stores or
handles a code-signing private key.

## Verification

- Every release includes `SHA256SUMS.txt` for the downloadable archive.
- Once signing is active, users can verify the executable with:

  ```bat
  signtool verify /pa Smage.exe
  ```

## Privacy and network access

Smage collects no telemetry and uploads no project data. Network access is
limited to user-initiated actions such as installing an optional extension
or downloading its models. See [PRIVACY.md](PRIVACY.md) for details.

## Reporting security issues

Please follow [SECURITY.md](SECURITY.md). Do not open a public issue for a
security problem.

## Attribution

When code signing is active, release downloads and this policy will state
the attribution required by the selected signing provider.

## Related documents

- [LICENSE](LICENSE) — GPL-3.0
- [NOTICE](NOTICE) — copyright and release approval
- [PRIVACY.md](PRIVACY.md) — privacy and network access
- [DISCLAIMER.md](DISCLAIMER.md) — research use, no clinical use, no warranty
- [SECURITY.md](SECURITY.md) — vulnerability reporting
