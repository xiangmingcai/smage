# Security Policy

## Supported versions

Smage is in early development. Security fixes are provided for the latest
release only.

| Version | Supported |
|---|---|
| 0.1.x   | Yes       |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public
report can be exploited before a fix is available.

Use GitHub's private vulnerability reporting:

1. Open <https://github.com/xiangmingcai/smage/security/advisories/new>
2. Describe the issue, the affected version, and how to reproduce it.

If you cannot use GitHub, email **xiangming_cai@126.com** with `[Smage
security]` in the subject.

Helpful information:

- Smage version and how it was installed (portable zip or source),
- Windows version,
- the steps to reproduce and the impact you expect,
- any log excerpt or proof-of-concept file (please keep it small and avoid
  real patient/experimental data).

## What to expect

This is a single-maintainer project, so responses are best effort:

- acknowledgement within 7 days,
- an assessment and, when confirmed, a fix in the next release,
- credit in the release notes unless you prefer to stay anonymous.

## Scope

Smage runs entirely locally and does not send data to a server. Relevant
areas include, for example, parsing untrusted data files (FCS, TIFF/OME,
parquet), project files exchanged between users, and code executed by
extension modules. Problems in third-party dependencies should be reported
to their own maintainers (see `THIRD_PARTY_NOTICES.md`), but a note to us is
welcome so we can update the dependency.
