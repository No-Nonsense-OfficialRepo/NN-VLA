"""Headless QML smoke check; does not open or join a real room."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
temporary = tempfile.TemporaryDirectory(prefix="nn-vla-ui-")
os.environ["LOCALAPPDATA"] = temporary.name
os.environ["NN_VLA_DATA"] = str(Path(temporary.name) / "NN VLA Data")
os.environ["SILENT_TUNNEL_HOME"] = str(Path(temporary.name) / "tunnel")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from vla_app.backend import Backend  # noqa: E402

QQuickStyle.setStyle("Basic")
app = QApplication([])
for font in (ROOT / "src/lantool/qt/assets/fonts").glob("*.ttf"):
    assert QFontDatabase.addApplicationFont(str(font)) >= 0, font
backend = Backend()
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
engine.rootContext().setContextProperty("backend", backend)
engine.load(QUrl.fromLocalFile(str(ROOT / "src/vla_app/Main.qml")))
assert engine.rootObjects(), "QML did not create its window: " + "\n".join(warnings)
window = engine.rootObjects()[0]
assert window.width() == 320 and window.height() == 320
assert window.flags() & Qt.FramelessWindowHint
assert window.minimumHeight() == 320 and window.maximumHeight() > 320
for screen in ("host", "options", "join", "options", "start"):
    backend.show(screen)
    app.processEvents()
    assert backend.state["screen"] == screen
QTimer.singleShot(750, app.quit)
app.exec()
if len(sys.argv) > 1:
    window.grabWindow().save(sys.argv[1])
assert not warnings, "QML warnings: " + "\n".join(warnings)
print("QML smoke check passed")
temporary.cleanup()
