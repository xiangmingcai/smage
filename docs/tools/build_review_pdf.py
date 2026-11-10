"""Render the review Markdown to a print-ready HTML file and a PDF.

Usage::

    .venv\\Scripts\\python.exe docs\\tools\\build_review_pdf.py

It reads ``docs/review/smage-documentation.md``, writes a styled
``docs/review/smage-documentation.html`` (images resolve relative to that
folder) and then asks Microsoft Edge or Google Chrome to print it to
``review/smage-documentation.pdf``.

If neither browser is found, the HTML is still written; open it and use
Ctrl+P → "Save as PDF".
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "review" / "smage-documentation.md"
HTML = ROOT / "docs" / "review" / "smage-documentation.html"
PDF = ROOT / "review" / "smage-documentation.pdf"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CSS = """
@page { size: A4; margin: 20mm 17mm 18mm 17mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.5; color: #1f2933; margin: 0;
}
.title-block { page-break-after: always; }
.cover { text-align: center; margin: 12pt 0 18pt; }
.cover img { width: 120px; height: 120px; }
h1 {
  page-break-before: always; font-size: 19pt; color: #1f2a3a;
  border-bottom: 2px solid #a1afc9; padding-bottom: 4pt; margin: 0 0 12pt;
}
h1.title { page-break-before: avoid; border: none; font-size: 24pt; margin: 0 0 14pt; }
h2 { font-size: 14pt; color: #2a3a52; margin: 16pt 0 6pt; }
h3 { font-size: 12pt; color: #2a3a52; margin: 12pt 0 4pt; }
p { margin: 5pt 0; }
ul, ol { margin: 4pt 0 6pt 18pt; padding: 0; }
li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #c9d2e0; padding: 4pt 6pt; vertical-align: top; }
th { background: #eef2f7; text-align: left; }
code { background: #f2f5f9; padding: 1pt 3pt; border-radius: 2pt; font-size: 0.9em; }
pre { background: #f6f8fb; border: 1px solid #e2e8f0; padding: 6pt 8pt; font-size: 9pt;
      white-space: pre-wrap; word-break: break-word; }
blockquote { border-left: 4px solid #a1afc9; background: #f7f9fc;
             margin: 8pt 0; padding: 5pt 10pt; }
img { max-width: 100%; max-height: 118mm; width: auto; height: auto;
      page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #d7dee8; margin: 12pt 0; }
.toc { page-break-after: always; }
.toc ul { list-style: none; margin: 0; padding-left: 0; }
.toc ul ul { padding-left: 14pt; }
.toc a { color: #1f2933; text-decoration: none; }
.toc li { margin: 2pt 0; }
a { color: #3f5f88; text-decoration: none; }
"""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Smage — documentation and review copy</title>
<style>{css}</style>
</head>
<body>
<div class="title-block">
<div class="cover"><img src="../assets/logo.png" alt="Smage"></div>
{front}
</div>
<nav class="toc"><h2 style="margin-top:0">Contents</h2>{toc}</nav>
{body}
</body>
</html>
"""


def _find_browser() -> str | None:
    for path in BROWSERS:
        if Path(path).is_file():
            return path
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _finalize_pdf() -> None:
    """Add a footer with page numbers, bookmarks and document metadata."""
    import html as html_module
    import re

    import pymupdf

    doc = pymupdf.open(PDF)
    total = doc.page_count

    for index, page in enumerate(doc):
        if index == 0:  # cover page
            continue
        text = (f"Smage — documentation and review copy · "
                f"page {index + 1} / {total}")
        size = 8
        width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        page.insert_text(
            (page.rect.width - width - 48, page.rect.height - 26),
            text, fontname="helv", fontsize=size, color=(0.38, 0.42, 0.50),
        )

    # Bookmarks from the H1/H2 headings in the body (after the table of
    # contents), mapped to the page where each heading first appears.
    body_html = HTML.read_text(encoding="utf-8").split("</nav>", 1)[-1]
    headings = re.findall(r"<h([12])[^>]*>(.*?)</h\1>", body_html, flags=re.S)
    pages_text = [" ".join(page.get_text().split()) for page in doc]
    body_start = next(
        (i for i, text in enumerate(pages_text)
         if "Smage is a free, open-source desktop application" in text),
        0,
    )
    bookmarks = []
    cursor = body_start
    for level, raw in headings:
        title = html_module.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        title = " ".join(title.split())
        if not title:
            continue
        page_index = None
        for i in range(cursor, len(pages_text)):
            if title in pages_text[i]:
                page_index = i
                break
        if page_index is None:
            page_index = cursor
        bookmarks.append([int(level), title, page_index + 1])
        cursor = page_index
    if bookmarks:
        doc.set_toc(bookmarks)

    doc.set_metadata({
        "title": "Smage — documentation and review copy",
        "author": "Xiangming Cai (Amsterdam UMC)",
        "subject": "Review copy of the Smage user documentation",
        "keywords": "Smage; flow cytometry; image cytometry; documentation; GPL-3.0",
        "creator": "Smage documentation build",
        "producer": "Microsoft Edge print-to-PDF",
    })

    temporary = PDF.with_suffix(".tmp.pdf")
    doc.save(str(temporary), garbage=3, deflate=True)
    doc.close()
    temporary.replace(PDF)


def _print_pdf(browser: str) -> int:
    PDF.parent.mkdir(parents=True, exist_ok=True)
    url = "file:///" + quote(HTML.as_posix())
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={PDF}",
        url,
    ]
    print("running:", " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(result.stderr[-2000:])
        return result.returncode
    if not PDF.is_file():
        print("browser finished but no PDF was written")
        return 1
    try:
        _finalize_pdf()
    except Exception as error:  # noqa: BLE001 - numbering is cosmetic
        print(f"could not add page numbers/bookmarks: {error!r}")
    print(f"wrote {PDF.relative_to(ROOT)} ({PDF.stat().st_size // 1024} KiB)")
    return 0


def main() -> int:
    try:
        import markdown
    except ImportError:
        print("the 'markdown' package is missing; install it with "
              "'pip install markdown' (MkDocs already depends on it)")
        return 1

    text = SOURCE.read_text(encoding="utf-8")
    marker = "\n# Part I — Overview"
    index = text.find(marker)
    if index == -1:
        front_md, body_md = "", text
    else:
        front_md = text[:index]
        body_md = text[index:]

    renderer = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "1", "permalink": False}},
    )
    body = renderer.convert(body_md)
    toc = renderer.toc
    front = markdown.Markdown(extensions=["extra", "sane_lists"]).convert(front_md)
    front = front.replace("<h1>", '<h1 class="title">', 1)
    html = TEMPLATE.format(css=CSS, front=front, toc=toc, body=body)
    HTML.write_text(html, encoding="utf-8")
    print(f"wrote {HTML.relative_to(ROOT)} ({HTML.stat().st_size // 1024} KiB)")

    browser = _find_browser()
    if browser is None:
        print("No Edge/Chrome found. Open the HTML file and use Ctrl+P → Save as PDF.")
        return 0
    return _print_pdf(browser)


if __name__ == "__main__":
    raise SystemExit(main())
