"""Build the single-file review copy of the documentation.

Usage::

    .venv\\Scripts\\python.exe docs\\tools\\build_review_md.py

Reads ``review/overview.md`` and the documentation pages listed below (in
navigation order) and writes ``review/smage-documentation.md``:

* page headings are demoted so the whole document has one hierarchy;
* cross-page links become plain text (a PDF has no second page);
* screenshot paths are rewritten relative to the output file;
* Mermaid diagrams become a plain "A → B → C" line;
* ``!!! note`` admonitions become blockquotes.

The result is meant to be converted to PDF (pandoc, or a browser's
"print to PDF") after review.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
REVIEW = ROOT / "review"
OVERVIEW = REVIEW / "overview.md"
OUTPUT = REVIEW / "smage-documentation.md"

# (section title, [(page title, path relative to docs/)])
SECTIONS = [
    ("Getting started", [
        ("Installation", "getting-started/installation.md"),
        ("Quick start", "getting-started/quickstart.md"),
        ("Projects and data", "getting-started/project-and-data.md"),
    ]),
    ("Guides", [
        ("Spectral image workflow", "guides/image-workflow.md"),
        ("Multiplex image workflow", "guides/multiplex-image-workflow.md"),
        ("FCS workflow", "guides/fcs-workflow.md"),
    ]),
    ("Modules", [
        ("Modules overview", "modules/index.md"),
        ("Data Import", "modules/import.md"),
        ("Rename", "modules/rename.md"),
        ("Subsample", "modules/subsample.md"),
        ("Subset", "modules/subset.md"),
        ("Compensation", "modules/compensation.md"),
        ("Unmix — Spectrum Extraction", "modules/unmix.md"),
        ("Unmix — Matrix Unmixing", "modules/unmix-matrix.md"),
        ("Illumination Correction", "modules/illumination.md"),
        ("Batch Align", "modules/batch-align.md"),
        ("Calculation", "modules/calculation.md"),
        ("Scale", "modules/scale.md"),
        ("Gating", "modules/gating.md"),
        ("Segmentation", "modules/segmentation.md"),
        ("Region", "modules/region.md"),
        ("Region Size", "modules/region-size.md"),
        ("Feature Down", "modules/feature-down.md"),
        ("Feature Up", "modules/feature-up.md"),
        ("Gate2Col", "modules/gate2col.md"),
        ("Cluster", "modules/cluster.md"),
        ("Dimensionality Reduction", "modules/dimred.md"),
        ("Data Clean", "modules/data-clean.md"),
        ("Figure", "modules/figure.md"),
        ("Heatmap", "modules/heatmap.md"),
        ("Export", "modules/export.md"),
    ]),
    ("Reference", [
        ("FAQ and troubleshooting", "reference/faq.md"),
        ("Release notes", "reference/release-notes.md"),
    ]),
    ("Development", [
        ("Build from source", "development/build.md"),
        ("Regression tests", "development/testing.md"),
        ("Packaging", "development/packaging.md"),
    ]),
    ("Contributing", [("Contributing", "contributing.md")]),
    ("License", [("License", "license.md")]),
]

_LINK_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+\.md)(#[^)]*)?\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_MERMAID = re.compile(r"^```mermaid\s*\n(.*?)\n```\s*$", re.MULTILINE | re.DOTALL)
_ADMONITION = re.compile(r"^!!!\s+(\w+)(?:\s+\"([^\"]*)\")?\s*$")
_HEADING = re.compile(r"^(#{1,5})(\s+.*)$")


def _strip_md_links(text: str) -> str:
    """Turn ``[text](page.md)`` into ``text`` — one page in a PDF."""
    def repl(match: re.Match) -> str:
        target = match.group(2)
        if target.startswith(("http://", "https://")):
            return match.group(0)
        return match.group(1)

    return _LINK_MD.sub(repl, text)


def _rewrite_images(text: str, page: Path) -> str:
    """Point screenshots at their real location relative to the output file."""
    def repl(match: re.Match) -> str:
        alt, src = match.group(1), match.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        target = (page.parent / src).resolve()
        rel = Path(os.path.relpath(target, OUTPUT.parent)).as_posix()
        return f"![{alt}]({rel})"

    return _IMAGE.sub(repl, text)


def _demote_headings(text: str, levels: int) -> str:
    """Push every heading down by *levels*, ignoring fenced code blocks."""
    out = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            out.append(line)
            continue
        if not fence:
            match = _HEADING.match(line)
            if match:
                hashes = "#" * min(6, len(match.group(1)) + levels)
                out.append(hashes + match.group(2))
                continue
        out.append(line)
    return "\n".join(out)


def _flatten_mermaid(text: str) -> str:
    """Replace a Mermaid block with a readable ``A → B → C`` line."""
    def repl(match: re.Match) -> str:
        labels = re.findall(r"\[([^\]]+)\]", match.group(1))
        seen = []
        for label in labels:
            if label not in seen:
                seen.append(label)
        return "_Process: " + " → ".join(seen) + "._"

    return _MERMAID.sub(repl, text)


def _flatten_admonitions(text: str) -> str:
    """Convert ``!!! type "Title"`` blocks into blockquotes."""
    lines = text.splitlines()
    out = []
    index = 0
    while index < len(lines):
        match = _ADMONITION.match(lines[index].strip())
        if not match:
            out.append(lines[index])
            index += 1
            continue
        kind = match.group(1).capitalize()
        title = match.group(2) or kind
        body = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                body.append("")
                index += 1
                continue
            if line.startswith("    "):
                body.append(line[4:])
                index += 1
                continue
            break
        while body and not body[-1]:
            body.pop()
        out.append(f"> **{title}.** {body[0].strip()}" if body else f"> **{title}.**")
        for line in body[1:]:
            out.append("> " + line if line else ">")
        out.append("")
    return "\n".join(out)


def _page_text(rel_path: str, levels: int) -> str:
    page = DOCS / rel_path
    text = page.read_text(encoding="utf-8").strip()
    text = _flatten_mermaid(text)
    text = _flatten_admonitions(text)
    text = _strip_md_links(text)
    text = _rewrite_images(text, page)
    text = _demote_headings(text, levels)
    return text


def main() -> int:
    if not OVERVIEW.is_file():
        print(f"missing {OVERVIEW}")
        return 1

    parts = [OVERVIEW.read_text(encoding="utf-8").strip(), "\n\n# Part II — Documentation\n"]

    for section, pages in SECTIONS:
        single = len(pages) == 1
        if not single:
            parts.append(f"\n## {section}\n")
        for title, rel_path in pages:
            levels = 1 if single else 2
            body = _page_text(rel_path, levels)
            heading = "#" * (2 if single else 3) + " " + title
            # Replace the page's own title (now demoted) with the section
            # title used in the navigation.
            lines = body.splitlines()
            if lines and re.match(r"^#{2,3}\s", lines[0]):
                lines = lines[1:]
                while lines and not lines[0].strip():
                    lines.pop(0)
            parts.append(heading + "\n\n" + "\n".join(lines) + "\n")
            parts.append("\n---\n")

    text = "\n".join(parts)
    text = re.sub(r"\n---\n\s*$", "\n", text)
    OUTPUT.write_text(text.rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} "
          f"({OUTPUT.stat().st_size // 1024} KiB, {text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
