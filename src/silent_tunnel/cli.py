"""Command line: python -m silent_tunnel [gui|status|join CODE|host|leave|install|uninstall|cleanup]"""
from __future__ import annotations

import argparse
import sys
import time

from ._version import __version__
from .errors import TunnelError
from .tunnel import Tunnel


def _say(msg: str) -> None:
    print(msg, flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="silent_tunnel", description=f"Silent Tunnel v{__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the launcher window (default)")
    sub.add_parser("status", help="show the current connection")
    j = sub.add_parser("join", help="join a room")
    j.add_argument("code")
    h = sub.add_parser("host", help="host a room until Ctrl+C")
    h.add_argument("--name", help="room name")
    sub.add_parser("leave", help="leave the current room")
    sub.add_parser("install", help="install VLA if missing")
    sub.add_parser("uninstall", help="remove VLA from this PC")
    sub.add_parser("cleanup", help="leave rooms and apply the remove-on-exit setting")
    args = p.parse_args(argv)
    cmd = args.cmd or "gui"

    if cmd == "gui":
        from lantool.launcher import main as launcher_main
        launcher_main()
        return 0

    t = Tunnel(progress=_say)
    try:
        if cmd == "status":
            st = t.status()
            _say(f"{st.state}: {st.message}")
            if st.connected:
                _say(f"  your IP: {st.ip}   host IP: {st.host_ip}")
        elif cmd == "join":
            st = t.join(args.code)
            _say(f"{st.state}: {st.message}")
            if st.connected:
                _say(f"  your IP: {st.ip}   host IP: {st.host_ip}")
            return 0 if st.connected else 2
        elif cmd == "host":
            room = t.open_room(args.name)
            _say(f"Room open. Share this code: {room.code}")
            _say(f"Host (game server) address: {room.host_ip}")
            _say("Press Ctrl+C to close the room.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            for note in t.cleanup():
                _say(note)
        elif cmd == "leave":
            t.leave()
        elif cmd == "install":
            t.ensure_ready()
            _say("VLA is installed and running.")
        elif cmd == "uninstall":
            _say("Removed." if t.uninstall_zerotier() else "VLA wasn't installed.")
        elif cmd == "cleanup":
            for note in t.cleanup():
                _say(note)
    except TunnelError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
