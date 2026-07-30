"""
Entry point for the Pagesont PAGE XML editor.

Usage:
    python viewer.py seg [file.xml]     # segmentation mode (edit polygons/baselines)
    python viewer.py text [file.xml]    # text proofreading mode
"""

import sys

from PyQt6.QtWidgets import QApplication

from main_window import MainWindow


def main():
    # Exactly one mode argument required: "seg" or "text"
    allowed = {"seg", "text"}
    if len(sys.argv) < 2 or sys.argv[1] not in allowed:
        print("Usage: python viewer.py <seg|text> [file.xml]")
        sys.exit(1)

    app = QApplication(sys.argv)

    # Translate CLI shorthand to internal mode strings used by MainWindow / PageView
    mode = "segmentation" if sys.argv[1] == "seg" else "text"
    win = MainWindow(mode=mode)
    win.show()

    # Optional second argument: path to a .xml file to load on startup
    if len(sys.argv) >= 3:
        win.open_file(sys.argv[2])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
