# Source provenance

NN VLA's `src/silent_tunnel`, `src/lantool/netcore`, `src/lantool/trust`, `src/lantool/presence`, and selected supporting modules and QML assets derive from [No-Nonsense LAN Launcher](https://github.com/No-Nonsense-OfficialRepo/NNLL), local checkout commit `7af98187a01ca86b70b8aba7f29f7e26b54ac1f0`, copied on 2026-11-14. That checkout had unrelated uncommitted changes, so this records the starting point rather than claiming an exact clean commit export.

Local adaptations include the tunnel-only `HostRoom` import, fixed No-Nonsense palette, connected-member blocking, live profile sharing, and the `vla_app` UI/controller. The standalone app and networking modules currently advertise version `1.0.11`; the beacon protocol is version `3`. This record identifies source provenance, not a verified native service build.
