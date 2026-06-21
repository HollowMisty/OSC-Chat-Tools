"""OSC Chat Tools - application entry point (modular rewrite).

Run with:  python main.py
"""
import sys


def run() -> None:
    from PySide6.QtWidgets import QApplication
    from oct.ui.main_window import MainWindow

    # Prevent a second instance (two senders fight over the VRChat chatbox).
    try:
        from tendo import singleton
        _instance = singleton.SingleInstance()  # exits the process if already running
    except ImportError:
        pass

    app = QApplication(sys.argv)
    window = MainWindow()
    if window.settings.minimizeOnStart:
        window.showMinimized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
