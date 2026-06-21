"""OSC Chat Tools - application entry point (modular rewrite).

Run with:  python main.py
"""
import os
import sys

# A windowed PyInstaller build (-w) has no console, so sys.stdout/sys.stderr are
# None. Libraries that write there (tqdm's download progress, stray prints) would
# crash with "'NoneType' object has no attribute 'write'". Give them a null sink.
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream, None) is None:
        try:
            setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))
        except Exception:
            pass

# Module-level so the single-instance lock lives for the whole process and isn't
# released early by garbage collection.
_instance = None


def _resource_path(name: str) -> str:
    """Path to a bundled resource: PyInstaller's temp dir when frozen, else the
    project root (next to this file)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def run() -> None:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    icon_path = _resource_path("oscicon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Prevent a second instance (two senders would fight over the VRChat chatbox).
    global _instance
    try:
        from tendo import singleton
        try:
            _instance = singleton.SingleInstance()
        except singleton.SingleInstanceException:
            QMessageBox.information(None, "OSC Chat Tools",
                                    "OSC Chat Tools is already running.")
            return  # exit this (second) instance gracefully
        except Exception:
            # Stale lock file or filesystem quirk - don't block startup over it.
            _instance = None
    except ImportError:
        _instance = None

    from oct.ui.main_window import MainWindow
    window = MainWindow()
    if window.settings.minimizeOnStart:
        window.showMinimized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
