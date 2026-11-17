# Third-party notices

NN VLA derives networking modules and visual assets from [No-Nonsense LAN Launcher](https://github.com/No-Nonsense-OfficialRepo/NN-LL). The starting source snapshot and local adaptations are recorded in [SOURCE-PROVENANCE.md](SOURCE-PROVENANCE.md). The project carries the GNU GPL v3 text in [LICENSE](LICENSE).

The bundled fonts retain their licence files beside the fonts:

- `src/lantool/qt/assets/fonts/Inter-OFL.txt`
- `src/lantool/qt/assets/fonts/Cinzel-OFL.txt`
- `src/lantool/qt/assets/fonts/CormorantGaramond-OFL.txt`

The Windows preview EXE is built with Python 3.15, PySide6/Qt 6.12, cryptography 50.0.2, and PyInstaller 6.22.3. Licence texts supplied with the installed Python, cryptography, and PyInstaller packages are copied verbatim under `licenses/`. The installed PySide6 wheel metadata declares `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`; see the [Qt for Python licensing documentation](https://doc.qt.io/qtforpython-6/) and [Qt 6.12 licensing page](https://doc.qt.io/qt-6.12/licensing.html) for component details. These notices identify bundled components; they are not a substitute for checking the obligations of the chosen distribution licence before publishing a binary.
