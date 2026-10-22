# Smage

Smage is a desktop application for analyzing **image and flow-cytometry data**.
It imports multiple data formats, converts them into a common tabular
representation, and provides analysis modules for manual gating, computation,
and visualization. Users compose these modules into analysis pipelines.

> **Status:** early development — not yet feature-complete.

## Planned features

- Import `.fcs` and tabular data into a common table format
- Interactive manual gating with hierarchical gates
- Data transforms, dimensionality reduction, and clustering
- Node-based analysis pipeline builder

## Project layout

```
smage/
├── src/smage/       # application source code
│   ├── __init__.py
│   └── main.py      # entry point (creates the main window)
├── requirements.txt # Python dependencies
└── README.md
```

## Development setup

Requirements: Python 3.11+.

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate it (Windows)
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app (pythonw keeps the console window hidden)
pythonw src\smage\main.py
```

## License

To be decided (planned for the initial public release).
