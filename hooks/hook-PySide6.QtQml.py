"""Collect the QML modules used by NN VLA instead of every installed Qt module."""
from pathlib import PurePath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()

_MODULES = {
    "qtcore",
    "qtqml",
    "qtqml/models",
    "qtqml/workerscript",
    "qtquick",
    "qtquick/window",
    "qtquick/layouts",
    "qtquick/templates",
    "qtquick/controls",
    "qtquick/controls/basic",
    "qtquick/controls/basic/impl",
    "qtquick/controls/impl",
    "qtquick/dialogs",
    "qtquick/dialogs/quickimpl",
}


def _used(item):
    destination = PurePath(item[1]).as_posix().lower()
    marker = "/qml/"
    return marker in destination and destination.split(marker, 1)[1] in _MODULES


binaries += [item for item in qml_binaries if _used(item)]
datas += [item for item in qml_datas if _used(item)]
