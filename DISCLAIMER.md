# Disclaimer and Intended Use

_Last updated: 2026-11-15_

Smage is free, open-source software for research use. This document
describes the intended use of the software, the responsibilities of its
users and how it is developed. It supplements the GNU General Public
License version 3 (see `LICENSE`) and the notices in `NOTICE`; it does not
change or restrict the rights granted by that license.

## Research use only

Smage is a research tool for spectral image and flow-cytometry data
analysis. It is **not a medical device** and has not been cleared,
certified or approved by any regulatory authority (including the FDA, EMA
or any other agency). It must not be used for clinical diagnosis, treatment
selection, patient monitoring, or any other clinical decision.

## No warranty

Smage is provided "as is", without warranty of any kind, express or
implied, including but not limited to the warranties of merchantability,
fitness for a particular purpose and non-infringement. See the GNU General
Public License version 3 for the full warranty disclaimer.

## Your responsibility

You are responsible for the analysis you perform and for every result,
interpretation and conclusion drawn from it. In particular, you are
responsible for:

- checking that the software, its algorithms and its outputs are correct
  and suitable for your data and research question;
- independently validating and reviewing results with qualified people
  before publication or any other use;
- data privacy, ethics or IRB approval, consent, and any regulatory or
  institutional requirements that apply to your data and use;
- keeping backups of raw data and project files;
- complying with the licenses of third-party components and extensions.

The authors and copyright holders accept no responsibility or liability
for decisions, publications or other consequences that follow from the use
of the software, to the extent permitted by applicable law.

## AI-assisted development and human collaboration

Smage is developed by a human maintainer with the assistance of AI coding
tools. AI assistants help draft code, tests, documentation and
refactorings. Human maintainers remain responsible for:

- architecture and design decisions;
- reviewing every change before it is merged;
- running and interpreting the automated regression and static checks;
- manually verifying user-interface and numerical results where relevant;
- deciding what is released and being accountable for the released code.

AI-generated code is never merged unreviewed. Smage contains no generative
AI features at runtime, sends no telemetry and does not upload user data;
AI tools are used during development only. Extensions that run local models
(for example SAM 3) are optional and run on the user's machine.

Contributors who use AI tools must disclose this in their contribution,
understand the code they submit well enough to maintain it, and follow the
same review and testing requirements as any other contribution.

## Third-party components and extensions

Third-party libraries and optional extensions keep their own licenses and
terms; see `THIRD_PARTY_NOTICES.md` and the documentation of each extension.
Installing or using an extension is subject to its own terms.

## Reporting problems

Bugs and other problems can be reported at
<https://github.com/xiangmingcai/smage/issues>. Security issues should follow
`SECURITY.md`. Support is best effort; there is no guarantee that a problem
will be fixed or answered.

## References

- `LICENSE` — GNU General Public License version 3
- `NOTICE` — copyright and redistribution notices
- `THIRD_PARTY_NOTICES.md` — third-party licenses
- `SECURITY.md` — vulnerability reporting
