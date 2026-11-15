"""Run the standalone tunnel window."""
from __future__ import annotations

import ctypes
import asyncio
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from PySide6 import QtSvg  # noqa: F401 - registers SVG support with Qt

from vla_app.backend import APP_DIR, Backend


def resource(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / relative


def main() -> int:
    self_test = "--self-test" in sys.argv
    if self_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QQuickStyle.setStyle("Basic")
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoNonsense.VLA")
    app = QApplication(sys.argv)
    qml_root = resource("lantool/qt/qml")
    for font in (qml_root.parent / "assets" / "fonts").glob("*.ttf"):
        if QFontDatabase.addApplicationFont(str(font)) < 0:
            raise RuntimeError(f"Could not load font: {font.name}")
    app.setFont(QFont("Inter"))
    icon = QIcon(str(qml_root.parent / "assets" / "launcher-icon.ico"))
    app.setWindowIcon(icon)
    backend = Backend()
    app.aboutToQuit.connect(backend.close)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(resource("vla_app/Main.qml"))))
    if not engine.rootObjects():
        if self_test:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "self-test.json").write_text(json.dumps({
                "window_count": 0,
                "qml_path": str(resource("vla_app/Main.qml")),
                "qml_exists": resource("vla_app/Main.qml").is_file(),
                "warnings": warnings,
            }, indent=2), encoding="utf-8")
        backend.close()
        return 1
    if self_test:
        worker_probe = asyncio.run_coroutine_threadsafe(
            asyncio.to_thread(lambda: "ok"), backend._loop
        ).result(timeout=5)
        QTimer.singleShot(700, app.quit)
    result = app.exec()
    if self_test:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / "self-test.json").write_text(json.dumps({
            "window_count": len(engine.rootObjects()),
            "width": engine.rootObjects()[0].width(),
            "height": engine.rootObjects()[0].height(),
            "frameless": bool(engine.rootObjects()[0].flags() & Qt.FramelessWindowHint),
            "minimum_height": engine.rootObjects()[0].minimumHeight(),
            "maximum_height": engine.rootObjects()[0].maximumHeight(),
            "worker_probe": worker_probe,
            "warnings": warnings,
        }, indent=2), encoding="utf-8")
        return 0 if (not warnings and result == 0 and worker_probe == "ok"
                     and engine.rootObjects()[0].flags() & Qt.FramelessWindowHint
                     and engine.rootObjects()[0].maximumHeight() > 320) else 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
