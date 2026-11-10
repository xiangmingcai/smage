# Build from source

Requirements: **Python 3.11, 64-bit, on Windows**.

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pythonw src\smage\main.py
```

`pythonw` keeps the console window hidden, like the packaged build.

## Project layout

```
src/smage/
├── core/        # Qt-free math and pipeline helpers
├── data/        # Qt-free data access, caches, background workers
├── widgets/     # reusable Qt widgets (plots, gate editor, panels)
├── modules/     # one package per analysis module
├── app/         # window, tabs, workflow tree, project I/O
├── frozen.py    # resource paths for the packaged build
├── main.py      # entry point
└── selfcheck.py # import self-check used by --self-test
packaging/       # PyInstaller spec, build script, icon/runners data
tests/regression # regression suite (see Testing)
```

The layers are enforced by convention: `core` and `data` never import Qt,
`widgets` never import modules, and module pages talk to the rest of the
application through the context object.

## Documentation site

The docs are built with MkDocs Material:

```bat
pip install mkdocs-material
mkdocs serve
```

Open <http://127.0.0.1:8000>. The GitHub Actions workflow publishes the site
on every push that changes `docs/` or `mkdocs.yml`.

## Code style

- Follow the surrounding code; keep functions in the existing style.
- UI text is English; comments explain *why*, not *what*.
- New cross-layer helper? Add it to `data/registry.py` instead of importing a
  module page from the data layer.
