from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication instance for tests that touch Qt (QImage/QGraphicsScene/...).

    Milestone 1-3 tests are pure domain/persistence and never needed this;
    Milestone 4's export renderer legitimately builds QGraphicsScene/QImage/
    QPainter objects (headlessly, via the offscreen Qt platform plugin) so
    those tests need a real, running QApplication.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
