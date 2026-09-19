"""PivotCut application entry point."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from pivotcut.app.main_window import MainWindow
from pivotcut.runtime_paths import resource_path
from pivotcut.ui.main_window_patch import install_ai_animation_action


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PivotCut")

    icon_path = resource_path("icons", "pivotcut.png")
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    install_ai_animation_action(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
