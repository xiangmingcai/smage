"""Extension registry UI and external-process helpers."""
from __future__ import annotations


from PySide6.QtCore import (
    QTimer,
    QUrl,
    Qt,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path
from smage.data.workers import _ProcessWorker
from smage.extensions_registry import (
    EXTENSION_DEFS,
    _extension_def,
)
import json
import queue
import threading
from smage.modules.batch_align import page as _batch_align_page
from smage.modules.cluster import page as _cluster_page
from smage.widgets import panels as _widgets_panels


def _nvidia_cuda_version():
    """Return the CUDA version (float) the installed NVIDIA driver supports,
    or None when no NVIDIA driver is found. Queries ``nvidia-smi``."""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi") or "nvidia-smi"
    try:
        out = subprocess.run(
            [exe],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        if "CUDA Version:" in line:
            try:
                return float(line.split("CUDA Version:")[1].split()[0])
            except (ValueError, IndexError):
                return None
    return None


def _pick_cuda_index(driver_cuda):
    """Return the best cuXXX index dir for a driver's CUDA version, or None."""
    if driver_cuda is None:
        return None
    for minor, dirname in _PYTORCH_CUDA_INDEXES:
        if minor <= driver_cuda:
            return dirname
    return None


class _ExtManagerDialog(QDialog):
    """Extension Manager dialog that warns before closing mid-operation."""

    def __init__(self, parent, busy_check) -> None:
        super().__init__(parent)
        self._busy_check = busy_check

    def closeEvent(self, event) -> None:
        if self._busy_check():
            reply = QMessageBox.question(
                self,
                "Operation in progress",
                "An installation is still running.\n"
                "Closing now will interrupt it and may leave the environment "
                "incomplete. Really close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        # Reject (not just accept) so the `finished` signal fires, which stops
        # the poll timer. Otherwise the timer keeps draining the shared worker
        # queue and steals results from other consumers (e.g. segmentation).
        self.reject()
        event.accept()



class ExtensionsMixin:
    def _ext_dir(self) -> Path:
        """User-level folder holding Smage's global (non-project) settings."""
        import os

        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Smage"

    def _ext_config_path(self) -> Path:
        return self._ext_dir() / "extensions.json"

    def _ext_config(self) -> dict:
        cache = getattr(self, "_ext_config_cache", None)
        if cache is None:
            try:
                cache = json.loads(
                    self._ext_config_path().read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                cache = {}
            if not isinstance(cache, dict):
                cache = {}
            self._ext_config_cache = cache
            self._migrate_ext_config(cache)
        return cache

    def _migrate_ext_config(self, cache) -> None:
        """Rename legacy extension ids to their new -cpu names.

        Cellpose/StarDist are now CPU methods (``cellpose-cpu`` /
        ``stardist-cpu``) with GPU variants arriving separately, so re-key any
        configuration saved under the old ids.
        """
        for old, new in (("cellpose", "cellpose-cpu"), ("stardist", "stardist-cpu")):
            if new not in cache and old in cache:
                cache[new] = cache.pop(old)

    def _ext_save_config(self) -> None:
        path = self._ext_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._ext_config(), indent=2), encoding="utf-8"
        )

    def _ext_entry(self, ext_id: str) -> dict:
        return self._ext_config().setdefault(ext_id, {})

    def _ext_python(self, ext_id: str) -> str:
        return str(self._ext_entry(ext_id).get("python") or "")

    def _ext_ready(self, ext_id: str) -> bool:
        """Whether an extension's environment, library and model are set up."""
        e = self._ext_entry(ext_id)
        entry = _extension_def(ext_id)
        needs_model = bool(entry and entry.get("model"))
        return bool(
            e.get("python")
            and e.get("verified")
            and (not needs_model or e.get("model_ready"))
        )

    def _ext_runner_path(self, ext_id: str) -> Path:
        entry = _extension_def(ext_id)
        return Path(__file__).parent / "runners" / entry["runner"]

    def _detect_python_interpreters(self) -> list:
        """Scan the system for Python interpreters (Windows).

        Returns a list of ``{"name": str, "path": str}`` entries, de-duplicated
        by path, ordered most-relevant first.
        """
        import glob
        import os
        import re
        import shutil
        import subprocess

        found = {}  # path -> display name

        def add(name, path):
            if not path:
                return
            path = str(path)
            # The WindowsApps python.exe/python3.exe are Store stubs, not real
            # interpreters; skip them so users don't pick a non-working Python.
            if "\\WindowsApps\\" in path or "/WindowsApps/" in path:
                return
            if path not in found and Path(path).is_file():
                found[path] = name

        # 1. Interpreters reachable on PATH and the `py` launcher.
        for cmd in ("python", "python3"):
            p = shutil.which(cmd)
            if p:
                add(f"Python on PATH ({cmd})", p)
        py_launcher = shutil.which("py")

        # 2. The `py` launcher lists every registered interpreter.
        if py_launcher:
            add("Python launcher (py)", py_launcher)
            try:
                out = subprocess.run(
                    [py_launcher, "-0p"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in (out.stdout or "").splitlines():
                    for token in line.split():
                        if re.match(r"^[A-Za-z]:[\\/].*python.*\.exe$", token):
                            add("Python (py launcher)", token)
            except Exception:  # noqa: BLE001
                pass

        # 3. Common install locations (python.org and conda).
        local = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("PROGRAMFILES")
        home = str(Path.home())

        if local:
            add(
                "Python (python.org)",
                Path(local) / "Programs" / "Python" / "python.exe",
            )
        for pattern in (
            (local and str(Path(local) / "Programs" / "Python" / "Python*" / "python.exe")),
            "C:/Python*/python.exe",
            "C:/Python*/Scripts/python.exe",
            (program_files and f"{program_files}/Python*/python.exe"),
        ):
            if pattern:
                for path in glob.glob(pattern):
                    add("Python (python.org)", path)

        for base in {home, local or home}:
            for name in ("miniconda3", "anaconda3", "Miniconda3", "Anaconda3"):
                add(name, Path(base) / name / "python.exe")
                add(name, Path(base) / name / "Scripts" / "python.exe")

        return [
            {"name": name, "path": path}
            for path, name in found.items()
        ]

    def _ext_worker(self):
        worker = getattr(self, "_ext_worker_obj", None)
        if worker is None:
            worker = _ProcessWorker()
            self._ext_worker_obj = worker
        return worker

    def _open_extension_manager(self, select: str | None = None) -> None:
        """Open the Extension Manager dialog (global, outside the workflow).

        ``select`` preselects an extension id (e.g. "flowsom").
        """
        dlg = _ExtManagerDialog(
            self, lambda: getattr(self, "_ext_busy", False)
        )
        dlg.setWindowTitle("Manage Extensions")
        dlg.resize(720, 460)
        lay = QHBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(10)

        list_widget = QListWidget()
        list_widget.setFixedWidth(160)
        list_widget.setStyleSheet(
            "QListWidget { background: #ffffff; border: 1px solid #d0d7e2; }"
        )
        for entry in EXTENSION_DEFS:
            list_widget.addItem(entry["name"])
        lay.addWidget(list_widget)

        host = QWidget()
        host.setLayout(QVBoxLayout())
        host.layout().setContentsMargins(0, 0, 0, 0)
        lay.addWidget(host, 1)

        def stop_timer():
            timer = getattr(host, "_ext_poll_timer", None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                host._ext_poll_timer = None

        def show(index):
            stop_timer()
            _widgets_panels._clear_layout(self._ctx, host.layout())
            if index < 0:
                return
            self._ext_detail_panel(host, EXTENSION_DEFS[index])

        list_widget.currentRowChanged.connect(show)
        if EXTENSION_DEFS:
            index = 0
            if select:
                index = next(
                    (
                        i for i, entry in enumerate(EXTENSION_DEFS)
                        if entry.get("id") == select
                    ),
                    0,
                )
            list_widget.setCurrentRow(index)

        # Stop the poll timer as soon as the manager is closed, so its callback
        # never touches widgets from a destroyed panel.
        dlg.finished.connect(lambda _=0: stop_timer())
        dlg.exec()
        # The extension state may have changed: refresh the open modules that
        # depend on FlowSOM (hints and the Calculate button).
        for index in range(1, self.tabs.count()):
            page = self.tabs.widget(index)
            if hasattr(page, "_ba_flowsom_status"):
                _batch_align_page._ba_update_flowsom_ext(self._ctx, page)
            if hasattr(page, "_cluster_ext_hint"):
                _cluster_page._cluster_update_ext_state(self._ctx, page)

    def _ext_detail_panel(self, host, entry) -> None:
        """Build the detail panel for one extension in the manager."""
        import os

        ext_id = entry["id"]
        e = self._ext_entry(ext_id)
        lay = host.layout()
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(6)

        lay.addWidget(QLabel(f"<b>{entry['name']}</b>"))
        lay.addWidget(QLabel(entry["desc"]))
        components = list(dict.fromkeys(
            [entry["pip"]] + list(entry.get("deps") or [])
        ))
        lay.addWidget(QLabel(
            "Runs in a separate Python environment; Smage does not bundle "
            + " / ".join(components) + "."
        ))
        note = (
            "You don't have to use the buttons below — you can install "
            "everything yourself in any Python environment, then set that "
            "environment's python.exe above. This only needs to be done once; "
            "after it succeeds, it keeps working."
        )
        if entry.get("family") in ("cellpose", "stardist"):
            note += (
                " If installation proves too difficult, you can instead run "
                "the cell segmentation in other software and import the "
                "resulting mask files with the Segmentation module's Mask Input."
            )
        self_note = QLabel(note)
        self_note.setWordWrap(True)
        self_note.setStyleSheet("color: #6b7785;")
        lay.addWidget(self_note)
        lay.addWidget(QLabel(""))
        lay.addWidget(QLabel("<b>1. Python environment</b>"))

        detect_python_btn = QPushButton("Detect installed Python…")
        detect_python_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(detect_python_btn)

        env_row = QWidget()
        eh = QHBoxLayout(env_row)
        eh.setContentsMargins(0, 0, 0, 0)
        eh.setSpacing(4)
        python_combo = QComboBox()
        python_combo.setEditable(True)
        python_combo.setPlaceholderText("Path to python.exe")
        if e.get("python"):
            python_combo.addItem(e.get("python"))
            python_combo.setEditText(e.get("python"))
        eh.addWidget(python_combo, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        eh.addWidget(browse_btn)
        lay.addWidget(env_row)

        install_box = QGroupBox("No suitable Python?")
        ib = QVBoxLayout(install_box)
        ib.setContentsMargins(8, 8, 8, 8)
        ib.setSpacing(4)
        box_hint = QLabel(
            "If you don't find a suitable python.exe, you can install Python "
            "or create an environment yourself. Otherwise ignore this box."
        )
        box_hint.setWordWrap(True)
        box_hint.setStyleSheet("color: #6b7785;")
        ib.addWidget(box_hint)
        ibtn = QHBoxLayout()
        install_python_btn = QPushButton("Install Python…")
        install_python_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        create_btn = QPushButton("Create venv…")
        create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ibtn.addWidget(install_python_btn)
        ibtn.addWidget(create_btn)
        ibtn.addStretch()
        ib.addLayout(ibtn)
        lay.addWidget(install_box)

        detect_btn = QPushButton("Detect library")
        detect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        detect_btn.setToolTip(
            f"Check that the chosen Python has the {entry['pip']} library installed"
        )
        lay.addWidget(detect_btn)

        status = QLabel(self._ext_status_text(entry, e))
        status.setWordWrap(True)
        status.setStyleSheet("color: #37474f;")
        lay.addWidget(status)

        lay.addWidget(QLabel(""))
        lay.addWidget(QLabel("<b>2. Install library</b>"))
        install_btn = QPushButton(f"Install {entry['pip']}")
        install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(install_btn)

        if entry.get("deps"):
            lay.addWidget(QLabel("<b>3. Install dependencies</b>"))
        if entry.get("device") == "gpu":
            gpu_note = QLabel(
                "The GPU build needs an NVIDIA GPU and a matching CUDA version "
                "of PyTorch, and the right version depends on your graphics "
                "driver. Smage can auto-detect the driver and pick the right "
                "version; if that does not work, choose a specific CUDA version "
                "below, or ask an AI assistant / an experienced colleague to "
                "install the environment for you. It only needs to succeed once."
            )
            gpu_note.setWordWrap(True)
            gpu_note.setStyleSheet("color: #6b7785;")
            lay.addWidget(gpu_note)
            lay.addWidget(QLabel("CUDA version"))
            cuda_combo = QComboBox()
            cuda_combo.addItem("Auto-detect (recommended)", "auto")
            cuda_combo.addItem("CPU only", "cpu")
            for minor, dirname in _PYTORCH_CUDA_INDEXES:
                cuda_combo.addItem(f"CUDA {minor:g} ({dirname})", dirname)
            idx = cuda_combo.findData(e.get("cuda", "auto"))
            cuda_combo.setCurrentIndex(max(0, idx))
            lay.addWidget(cuda_combo)

            def on_cuda_changed(value):
                e["cuda"] = value
                self._ext_save_config()

            cuda_combo.currentIndexChanged.connect(
                lambda _=0: on_cuda_changed(cuda_combo.currentData())
            )
        if entry.get("deps"):
            install_deps_btn = QPushButton("Install dependencies")
            install_deps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            install_deps_btn.setToolTip(
                "Force-reinstall the framework packages ("
                + ", ".join(entry.get("deps") or []) + ") even if already present"
            )
            lay.addWidget(install_deps_btn)
        else:
            install_deps_btn = None

        if entry.get("model"):
            lay.addWidget(QLabel("<b>4. Download model</b>"))
            model_btn = QPushButton("Download model")
            model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(model_btn)
        else:
            model_btn = None

        progress = QProgressBar()
        progress.setRange(0, 0)  # indeterminate while a command is running
        progress.setTextVisible(False)
        progress.setFixedHeight(14)
        progress.hide()
        lay.addWidget(progress)

        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlaceholderText("Output appears here…")
        log.setStyleSheet("background: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace;")
        lay.addWidget(log, 1)

        worker = self._ext_worker()
        page_state = {"busy": False}

        def set_busy(busy, show_progress=False):
            page_state["busy"] = busy
            self._ext_busy = bool(busy)
            if busy and show_progress:
                progress.setRange(0, 0)
                progress.show()
            else:
                progress.hide()
            for b in (
                browse_btn, detect_python_btn, install_python_btn, create_btn,
                detect_btn, install_btn, install_deps_btn, model_btn,
            ):
                if b is not None:
                    b.setEnabled(not busy)

        def current_python():
            p = python_combo.currentText().strip()
            if not p and e.get("python"):
                p = e.get("python")
            return p

        def on_detect():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Detect", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True)  # detect is quick: no progress bar
            log.clear()
            log.appendPlainText(
                f"Checking {entry['pip']} and its dependencies…"
            )
            # Check package metadata only (fast); do not import the library,
            # since importing TensorFlow / PyTorch can take tens of seconds.
            modules = list(entry.get("deps") or []) + [entry["pip"]]
            probe = "\n".join([
                "import importlib.metadata as md",
                "mods = " + repr(modules),
                "for n in mods:",
                "    try:",
                "        print(n, md.version(n))",
                "    except Exception:",
                "        print(n, 'MISSING')",
            ])
            # GPU extensions additionally verify that PyTorch/TensorFlow can
            # actually use CUDA (this imports the framework, so it is slower).
            if entry.get("device") == "gpu":
                log.appendPlainText("Also checking CUDA availability…")
                probe += "\n" + "\n".join([
                    "try:",
                    "    import torch",
                    "    print('gpu-torch-version', torch.__version__)",
                    "    print('gpu-cuda-version', torch.version.cuda or 'NONE')",
                    "    print('gpu-cuda-available', "
                    "'True' if torch.cuda.is_available() else 'False')",
                    "    if torch.cuda.is_available():",
                    "        print('gpu-device-name', torch.cuda.get_device_name(0))",
                    "    else:",
                    "        print('gpu-device-name', '(none)')",
                    "except Exception as exc:",
                    "    print('gpu-check-error', repr(exc))",
                ])
            worker.run(("detect", ext_id), [python, "-c", probe])

        def on_install():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Install", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            log.appendPlainText(
                f"Installing {entry['pip']} into {python}…\n"
                "This can take several minutes."
            )
            worker.run(
                ("install", ext_id),
                [python, "-m", "pip", "install", entry["pip"]],
            )

        def on_install_deps():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Install dependencies", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            deps = list(entry.get("deps") or [])
            argv = [python, "-m", "pip", "install", "--force-reinstall"]
            index_url = None
            if entry.get("device") == "gpu":
                choice = e.get("cuda", "auto")
                if choice == "cpu":
                    log.appendPlainText(
                        "CPU only selected — installing the default (CPU) build."
                    )
                elif choice.startswith("cu"):
                    index_url = f"https://download.pytorch.org/whl/{choice}"
                else:  # auto-detect the driver's supported CUDA version
                    driver_cuda = _nvidia_cuda_version()
                    if driver_cuda is None:
                        log.appendPlainText(
                            "No NVIDIA driver detected — installing CPU-only "
                            "build. If you have an NVIDIA GPU, install/update "
                            "its driver, or choose a specific CUDA version."
                        )
                    else:
                        picked = _pick_cuda_index(driver_cuda)
                        if picked is None:
                            log.appendPlainText(
                                f"Driver CUDA {driver_cuda:g} is too old for any "
                                "supported CUDA build — installing CPU-only build."
                            )
                        else:
                            index_url = f"https://download.pytorch.org/whl/{picked}"
                            log.appendPlainText(
                                f"Detected NVIDIA driver CUDA {driver_cuda:g} → "
                                f"using {picked}."
                            )
                if index_url:
                    argv += ["--index-url", index_url]
                argv += deps
            else:
                argv += deps
            log.appendPlainText(
                f"Reinstalling {' '.join(deps)} into {python}…\n"
                + (f"Using index: {index_url}\n" if index_url else "")
                + "This can take several minutes."
            )
            worker.run(("deps", ext_id), argv)

        def on_download_model():
            python = current_python()
            if not python:
                QMessageBox.information(
                    self, "Download model", "Choose a Python environment first."
                )
                return
            e["python"] = python
            self._ext_save_config()
            set_busy(True, show_progress=True)
            log.clear()
            log.appendPlainText(f"Downloading {entry['model']} model…")
            worker.run(
                ("model", ext_id),
                [python, str(self._ext_runner_path(ext_id)),
                 "download-model", "--model", entry["model"]],
            )

        def browse():
            path, _ = QFileDialog.getOpenFileName(
                self, "Select python.exe", "",
                "Python (python.exe);;All files (*)",
            )
            if path:
                python_combo.setEditText(path)

        def detect_python():
            found = self._detect_python_interpreters()
            if not found:
                QMessageBox.information(
                    self,
                    "Python not found",
                    "No Python installation was found on this computer.\n\n"
                    "Click 'Install Python…' to download and install Python, "
                    "then come back and click 'Detect installed Python…'.",
                )
                return
            current = python_combo.currentText().strip()
            python_combo.clear()
            for item in found:
                python_combo.addItem(item["path"])
            if current in [python_combo.itemText(i) for i in range(python_combo.count())]:
                python_combo.setCurrentText(current)
            else:
                python_combo.setCurrentIndex(0)

        def install_python():
            QDesktopServices.openUrl(QUrl("https://www.python.org/downloads/"))
            QMessageBox.information(
                self,
                "Install Python",
                "1. Download the installer from python.org.\n"
                "2. Run it and tick 'Add python.exe to PATH'.\n"
                "3. After installing, click 'Detect installed Python…' here "
                "to use the new Python.",
            )

        def create_venv():
            base, _ = QFileDialog.getOpenFileName(
                self, "Select a base python.exe to create a venv", "",
                "Python (python.exe);;All files (*)",
            )
            if not base:
                return
            target = QFileDialog.getExistingDirectory(
                self, "Choose a folder for the new environment"
            )
            if not target:
                return
            venv = str(Path(target) / f"smage-{ext_id}")
            log.clear()
            log.appendPlainText(f"Creating venv at {venv}…")
            set_busy(True, show_progress=True)
            worker.run(
                ("create", ext_id, venv),
                [base, "-m", "venv", venv],
            )

        def refresh_status():
            status.setText(self._ext_status_text(entry, e))
            if self._ext_ready(entry["id"]):
                status.setStyleSheet("color: #1a7f37; font-weight: bold;")
            elif e.get("verified") is False and (e.get("missing") or not e.get("python")):
                status.setStyleSheet("color: #c0392b; font-weight: bold;")
            else:
                status.setStyleSheet("color: #37474f;")

        def on_poll():
            for item in worker.poll():
                if item[1] == "line":
                    log.appendPlainText(item[2])
                    continue
                _tag, kind, code, text = item[0], item[1], item[2], item[3]
                key = kind  # first element of the request key tuple
                set_busy(False)
                # Output was already streamed line-by-line above; only surface
                # the captured text when the subprocess died without streaming
                # anything (e.g. an OSError before Popen), signalled by code -1.
                if code < 0 and text:
                    log.appendPlainText(text.rstrip())
                if isinstance(_tag, tuple) and _tag[0] == "detect":
                    missing = []
                    version = ""
                    gpu = {}
                    for line in text.strip().splitlines():
                        parts = line.split(None, 1)
                        if len(parts) != 2:
                            continue
                        name, value = parts[0], parts[1]
                        if name.startswith("gpu-"):
                            gpu[name] = value
                            continue
                        if value == "MISSING":
                            missing.append(name)
                        elif name == entry["pip"]:
                            version = value
                    if code == 0 and not missing:
                        e["verified"] = True
                        e["version"] = version
                        e["missing"] = []
                    else:
                        e["verified"] = False
                        e["version"] = ""
                        e["missing"] = missing
                        if missing:
                            log.appendPlainText(
                                "Missing: " + ", ".join(missing) + ".\n"
                                f"Run 'Install {entry['pip']}' to install them."
                            )
                        else:
                            log.appendPlainText(
                                "Could not check this environment. "
                                "Verify the Python path and try again."
                            )
                    # GPU extensions: report whether CUDA is actually usable.
                    if entry.get("device") == "gpu":
                        torch_ver = gpu.get("gpu-torch-version", "")
                        cuda_ver = gpu.get("gpu-cuda-version", "NONE")
                        avail = gpu.get("gpu-cuda-available", "False") == "True"
                        dev = gpu.get("gpu-device-name", "(none)")
                        err = gpu.get("gpu-check-error")
                        cpu_build = (cuda_ver == "NONE") or ("+cpu" in torch_ver)
                        log.appendPlainText("")
                        log.appendPlainText("GPU check:")
                        if err is not None:
                            log.appendPlainText(
                                f"  ✗ Could not import torch: {err}"
                            )
                            e["gpu_ok"] = False
                        else:
                            if cpu_build:
                                log.appendPlainText(
                                    f"  ✗ PyTorch build: CPU-only ({torch_ver})"
                                )
                            else:
                                log.appendPlainText(
                                    f"  ✓ PyTorch build: GPU (CUDA {cuda_ver})"
                                )
                            if avail:
                                log.appendPlainText(
                                    f"  ✓ CUDA available: True ({dev})"
                                )
                            else:
                                log.appendPlainText("  ✗ CUDA available: False")
                            e["gpu_ok"] = (not cpu_build) and avail
                        if e.get("gpu_ok"):
                            log.appendPlainText("  ✓ Ready to run on GPU")
                        else:
                            log.appendPlainText(
                                "  ✗ Not ready for GPU — will fall back to CPU"
                            )
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "install":
                    e["verified"] = code == 0
                    if code == 0:
                        log.appendPlainText("Installed. Run Detect library to confirm.")
                    else:
                        log.appendPlainText(
                            "Installation failed. Check the messages above."
                        )
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "deps":
                    if code == 0:
                        log.appendPlainText(
                            "Dependencies installed. Run Detect library to confirm."
                        )
                    else:
                        log.appendPlainText(
                            "Dependency installation failed. Check the messages above."
                        )
                elif isinstance(_tag, tuple) and _tag[0] == "model":
                    e["model_ready"] = code == 0
                    self._ext_save_config()
                elif isinstance(_tag, tuple) and _tag[0] == "create":
                    if code == 0:
                        venv_python = str(
                            Path(_tag[2]) / ("Scripts" if os.name == "nt" else "bin")
                            / ("python.exe" if os.name == "nt" else "python")
                        )
                        python_combo.setEditText(venv_python)
                        e["python"] = venv_python
                        self._ext_save_config()
                        log.appendPlainText(f"Environment created: {venv_python}")
                    else:
                        log.appendPlainText("Could not create the environment.")
                refresh_status()

        poll_timer = QTimer(host)
        poll_timer.setInterval(150)
        poll_timer.timeout.connect(on_poll)
        poll_timer.start()

        browse_btn.clicked.connect(browse)
        detect_python_btn.clicked.connect(detect_python)
        install_python_btn.clicked.connect(install_python)
        create_btn.clicked.connect(create_venv)
        detect_btn.clicked.connect(on_detect)
        install_btn.clicked.connect(on_install)
        if install_deps_btn is not None:
            install_deps_btn.clicked.connect(on_install_deps)
        if model_btn is not None:
            model_btn.clicked.connect(on_download_model)
        host._ext_poll_timer = poll_timer
        refresh_status()

    def _ext_status_text(self, entry, e) -> str:
        python = e.get("python") or ""
        if not python:
            return "Not configured — no Python environment."
        parts = [f"Python: {python}"]
        missing = e.get("missing")
        if e.get("verified"):
            parts.append(f"✓ {entry['name']} detected: {e.get('version') or 'ok'}")
        elif missing:
            parts.append("✗ Missing packages: " + ", ".join(missing))
        else:
            parts.append("Not checked yet — click 'Detect library'.")
        if entry.get("model"):
            if e.get("model_ready"):
                parts.append(f"Model: {entry['model']} ready")
            else:
                parts.append("Model: not downloaded")
        if entry.get("device") == "gpu" and "gpu_ok" in e:
            if e["gpu_ok"]:
                parts.append("✓ GPU: CUDA available")
            else:
                parts.append("✗ GPU: CUDA not available (CPU-only build)")
        if self._ext_ready(entry["id"]):
            parts.append("✓ Ready to use")
        return "\n".join(parts)

    def _run_extension_process(self, page, q, argv, cancel_flag,
                               name="Extension") -> None:
        """Run an external extension command, streaming its output.

        Cancel kills the process promptly, even while it prints nothing.
        """
        import subprocess

        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = queue.Queue()

        def reader():
            for raw in proc.stdout:
                lines.put(raw)
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()
        try:
            while True:
                try:
                    raw = lines.get(timeout=0.1)
                except queue.Empty:
                    raw = ""
                if raw is None:
                    break
                line = (raw or "").strip()
                if line:
                    q.put(("log", line))
                if getattr(page, cancel_flag, False):
                    raise ValueError("Calculation cancelled.")
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if code != 0:
            raise ValueError(f"{name} exited with code {code}")

    def _flowsom_external(self, page, q, X, xdim, ydim, k, seed,
                          cancel_flag="_cluster_cancel") -> dict:
        """Run FlowSOM in the extension environment; return its arrays.

        Shared by Cluster (FlowSOM method) and Batch Alignment (CytoNorm
        training). Smage never imports flowsom itself.
        """
        import tempfile
        from pathlib import Path as _Path

        import numpy as np

        if not self._ext_ready("flowsom"):
            raise ValueError(
                "FlowSOM extension is not configured. Open Extension → "
                "Manage Extensions and set it up first."
            )
        python = self._ext_python("flowsom")
        runner = self._ext_runner_path("flowsom")
        q.put(("log", f"FlowSOM external run: grid {xdim}×{ydim}, k={k}, "
                      f"python={python}"))
        with tempfile.TemporaryDirectory(prefix="smage-flowsom-") as tmp:
            matrix = _Path(tmp) / "matrix.npy"
            result = _Path(tmp) / "result.npz"
            # Keep the caller's dtype: Cluster passes float64 and CytoNorm
            # float32, exactly as the previous in-process calls did.
            np.save(matrix, np.ascontiguousarray(X))
            argv = [
                python, str(runner), "run",
                "--input", str(matrix), "--output", str(result),
                "--xdim", str(int(xdim)), "--ydim", str(int(ydim)),
                "--k", str(int(k)), "--seed", str(int(seed)),
            ]
            self._run_extension_process(
                page, q, argv, cancel_flag, name="FlowSOM"
            )
            if not result.is_file():
                raise ValueError("FlowSOM did not produce a result file")
            with np.load(result) as data:
                return {
                    "cell_cluster": np.asarray(
                        data["cell_cluster"], dtype="int64"
                    ),
                    "node_cluster": np.asarray(
                        data["node_cluster"], dtype="int32"
                    ),
                    "codes": np.asarray(data["codes"], dtype="float32"),
                    "n_clusters": int(data["n_clusters"]),
                }

    def _parc_external(self, page, q, X, knn, resolution, seed,
                       cancel_flag="_cluster_cancel"):
        """Run PARC in the extension environment; return 1-based labels."""
        import tempfile
        from pathlib import Path as _Path

        import numpy as np

        if not self._ext_ready("parc"):
            raise ValueError(
                "PARC extension is not configured. Open Extension → "
                "Manage Extensions and set it up first."
            )
        python = self._ext_python("parc")
        runner = self._ext_runner_path("parc")
        q.put(("log", f"PARC external run: knn={knn}, resolution={resolution}, "
                      f"python={python}"))
        with tempfile.TemporaryDirectory(prefix="smage-parc-") as tmp:
            matrix = _Path(tmp) / "matrix.npy"
            result = _Path(tmp) / "result.npz"
            np.save(matrix, np.ascontiguousarray(X))
            argv = [
                python, str(runner), "run",
                "--input", str(matrix), "--output", str(result),
                "--knn", str(int(knn)),
                "--resolution", str(float(resolution)),
                "--seed", str(int(seed)),
            ]
            self._run_extension_process(
                page, q, argv, cancel_flag, name="PARC"
            )
            if not result.is_file():
                raise ValueError("PARC did not produce a result file")
            with np.load(result) as data:
                labels = np.asarray(data["labels"], dtype="int64")
        if labels.size != X.shape[0]:
            raise ValueError(
                "PARC returned an unexpected number of labels "
                f"({labels.size} for {X.shape[0]} rows)"
            )
        return labels + 1


_PYTORCH_CUDA_INDEXES = [
    (12.8, "cu128"),
    (12.6, "cu126"),
    (12.4, "cu124"),
    (12.1, "cu121"),
    (11.8, "cu118"),
]

