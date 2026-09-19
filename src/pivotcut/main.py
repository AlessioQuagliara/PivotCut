"""PivotCut application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from pivotcut.app.main_window import MainWindow
from pivotcut.ui.main_window_patch import install_ai_animation_action


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PivotCut")
    window = MainWindow()
    install_ai_animation_action(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
