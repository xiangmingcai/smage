# Contributing to Smage

Thanks for helping improve Smage. This page covers how to report problems,
how to send a pull request, and the few conventions we follow.

## Reporting bugs and asking for features

- Use the [issue tracker](https://github.com/xiangmingcai/smage/issues).
- For a bug, please include:
  - Smage version (shown in `Help → About`, or the release you downloaded),
  - Windows version and whether you use the portable build or the source,
  - steps to reproduce, expected and actual behaviour,
  - the newest log file from `<project>.smage/log/` — it holds tracebacks and
    render timings that make most problems diagnosable without a screen share.
- **Do not open a public issue for a security problem** — see
  [SECURITY.md](SECURITY.md).

## Building and testing

Requirements: Python 3.11, 64-bit, on Windows.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pythonw src\smage\main.py
```

The regression suite lives in `tests\regression` and never writes to your
projects — it uses `E:\smage_test\samge_batch_test` (configurable via
`SMAGE_TEST_ROOT`).

```bat
.venv\Scripts\python.exe tests\regression\run_all.py --smoke
.venv\Scripts\python.exe tests\regression\run_all.py --module gating
.venv\Scripts\python.exe tests\regression\run_all.py --full
```

Please run at least `--module <area>` while developing and `--full` before
opening a pull request.

## Pull requests

- Keep a pull request focused on one change; split unrelated cleanups.
- Add or update a regression case when you fix a bug or add behaviour.
- Update `CHANGELOG.md` under `Unreleased`.
- Match the existing style: layered packages (`core/` is Qt-free, `data/` is
  Qt-free, `widgets/`, `modules/`, `app/`), English UI text, and no new
  third-party dependency without a note in the PR.
- All commits must be signed off — see below.

## DCO (Developer Certificate of Origin)

Smage uses the **DCO, not a CLA**. The DCO is a one-line statement, added to
each commit, that you wrote the change (or have the right to submit it) and
that it may be distributed under the project's license (GPL-3.0):

```
Signed-off-by: Your Name <you@example.com>
```

Add it automatically with:

```bat
git commit -s -m "Fix gate statistics refresh"
```

If you forgot, `git commit --amend -s` (or an interactive rebase with
`--signoff`) fixes the branch before it is merged. Pull requests without a
sign-off will be asked to add one.

A CLA would only be needed if the project ever wanted to relicense
contributions (for example to sell a commercial license); Smage has no such
plan, so the lighter DCO is enough.

## Security

Please report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/xiangmingcai/smage/security/advisories/new)
rather than in a public issue. Details are in [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contribution is licensed under
GPL-3.0, the license of the project.
