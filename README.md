# NN VLA

Standalone Windows room tunnelling UI using the No-Nonsense LAN Launcher theme and its existing VLA room protocol components. The borderless window starts at 320 × 320 and can be dragged taller to show more users at once; the room and options views scroll when needed. This repository is a **development build**, not a complete distributable release.

## Implemented source

- Host or Join from one window; Host and Join share an Options screen.
- Host and Join keep the selected role for the current run. Close and reopen the app to choose the other role; Options has a return control.
- Hosts manage blocked users from Options; the Host view keeps the room action full width.
- Signed LAN discovery, 16-hex room-key joining, optional password admission with approval after three wrong attempts, and a room-scoped blacklist.
- Copyable room keys and VLA IPs, profile name and avatar, room roster, peer TCP round-trip checks, kick-only or kick-and-block, and a basic network diagnostic.
- Profile name edits stay in the field while the roster updates. The picture editor supports NNLL-style square crop, zoom, and drag-to-pan, with its actions fitting the compact window; saved names and avatars update connected peers. Checkbox indicators are 13 px.
- Room action buttons use green for opening/joining, amber with Retry after errors, and red for closing/leaving. There is no separate status row; hovering the action shows its status and guidance in NNLL-style help.
- Leaving or closing a room tears down its connection state. The window and EXE use NNLL's icon; UI controls and fonts are copied from NNLL.

## Current release blockers

The copied native backend still uses NNLL's `LanLauncherVLA` service identity, paths, elevation helper, and shared driver ownership records. This app must have its own native service build, package manifest, and owner checks before its requested **Remove VLA on exit** setting can be enabled. The setting is shown disabled in this development UI. Automatic deletion of NNLL's service would be unsafe.

The portable EXE does **not** include the native service or driver yet. If an owned compatible service is already installed, the room paths can be exercised. A fresh PC cannot yet install VLA from this build. The app advertises version 1.0.11 and does not claim NNLL cross-app compatibility. When frozen, app settings and room state are stored in `NN VLA Data` beside the EXE; run it from a writable folder.

## Build on Windows

Use Windows x64 Python 3.15.0, then run `./build.ps1 -Python '<full path to python.exe>'`. The build uses `build-requirements.lock` with wheel hashes and produces `dist/NN VLA V<version>.exe` and `dist/manifest.json`. It bumps the patch version for each new build, embeds that version in the Windows resource, runs a packaged window and background worker smoke test, and removes superseded EXEs after success when they are not running. V1.0.11 is 79,516,649 bytes (75.8 MiB), reduced from the initial 210 MB package by collecting only the needed Qt modules and using Qt for image cropping. The exact V1.0.11 EXE passed a targeted Microsoft Defender scan with zero detections at signature `1.463.299.0`; its hash and smoke result are recorded in `dist/validation.json`. Native package assembly and two-PC validation under normal Windows security settings remain outstanding.

## Run from source

From the project root in PowerShell with Windows x64 Python 3.15:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m vla_app
```

Run the source UI check with `.\.venv\Scripts\python.exe tests\ui_smoke.py`. Hosting or joining still requires a compatible native VLA service already installed on the PC.

See [GITHUB_UPLOAD.md](GITHUB_UPLOAD.md) for the prepared source and preview release packages.

## Source and licences

Network modules and QML assets derive from the No-Nonsense LAN Launcher source; see [SOURCE-PROVENANCE.md](SOURCE-PROVENANCE.md). The project licence is in [LICENSE](LICENSE). Font licences are under `src/lantool/qt/assets/fonts/`, and other notices and included licence texts are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). NNLL's native driver files are not included.
