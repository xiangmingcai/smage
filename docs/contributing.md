# Contributing

Bug reports, feature requests and pull requests are welcome.

- **Issues:** <https://github.com/xiangmingcai/smage/issues>
- **Security problems:** report them privately via GitHub Security Advisories
  (see [SECURITY.md](https://github.com/xiangmingcai/smage/blob/main/SECURITY.md)),
  never as a public issue.

## Pull requests

1. Keep the change focused.
2. Add or update a regression case.
3. Run `tests/regression/run_all.py --full`.
4. Update `CHANGELOG.md` under `Unreleased`.
5. Sign off every commit (DCO):

   ```bat
   git commit -s -m "Fix gate statistics refresh"
   ```

The full guidelines live in
[CONTRIBUTING.md](https://github.com/xiangmingcai/smage/blob/main/CONTRIBUTING.md).

## AI-assisted contributions

AI coding tools may be used when preparing a contribution. If you use one:

- say so in the pull request;
- make sure you understand the change well enough to maintain it;
- keep the same test, documentation and review requirements as any other
  contribution — AI-generated code is never merged unreviewed;
- do not include generated text or code whose license is incompatible with
  GPL-3.0.

The maintainer remains accountable for everything that is released. See
[DISCLAIMER.md](https://github.com/xiangmingcai/smage/blob/main/DISCLAIMER.md)
for the full statement on AI-assisted development and human review.

## License

Contributions are licensed under GPL-3.0, the license of the project.
