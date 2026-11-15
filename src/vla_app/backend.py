"""Tunnel-only Qt bridge. Network work lives on a dedicated asyncio loop."""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import os
import secrets
import socket
import sys
import threading
import time
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QObject, Property, Signal, Slot, Qt, QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication, QFileDialog

from lantool._version import __version__
from lantool.netcore.admission import AdmissionAccess, RoomAdmission, request_admission
from lantool.netcore.beacon import BeaconListener
from lantool.netcore.discovery import fetch_room_when_ready
from lantool.netcore.host_room import HostRoom
from lantool.presence.client import PresenceClient
from lantool.presence.server import PresenceServer
from lantool.trust.signing import generate_keypair
from silent_tunnel import Tunnel, format_room_code, parse_room_code


PORT_PING = 47808
# Python 3.15 may leave this attribute as a lazy_import sentinel after Qt loads.
# asyncio.to_thread expects the concrete class when it creates its executor.
concurrent.futures.ThreadPoolExecutor = ThreadPoolExecutor

def _app_dir() -> Path:
    override = os.environ.get("NN_VLA_DATA")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "NN VLA Data"
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NN VLA"


APP_DIR = _app_dir()


def _lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
        except OSError:
            return ""


def _load_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default.copy()
    except (OSError, ValueError):
        return default.copy()


class Backend(QObject):
    changed = Signal()
    _patch = Signal(object)

    def __init__(self):
        super().__init__()
        self._patch.connect(self._apply, Qt.ConnectionType.QueuedConnection)
        self._pc_name = socket.gethostname()[:32] or "PC"
        self._settings_path = APP_DIR / "options.json"
        self._settings = _load_json(self._settings_path, {"name": self._pc_name,
                                                          "avatar": "", "wipe_on_exit": True})
        self._settings.setdefault("name", self._pc_name)
        self._settings.setdefault("avatar", "")
        self._settings.setdefault("wipe_on_exit", True)
        self._state = {"screen": "start", "role": "", "connected": False,
                       "busy": False, "roomError": False, "status": "Ready", "roomKey": "",
                       "players": [], "blacklist": [], "approval": None,
                       "hostChoices": [], "passwordPrompt": False,
                       "diagnostic": [], "name": self._settings["name"],
                       "avatar": self._settings["avatar"],
                       "wipeOnExit": self._settings["wipe_on_exit"],
                       "preview": False}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        os.environ.setdefault("SILENT_TUNNEL_HOME", str(APP_DIR / "tunnel"))
        self._tunnel = Tunnel(progress=lambda line: self._emit(status=line))
        self._room = None
        self._admission = None
        self._presence_server = None
        self._presence_client = None
        self._host_room = None
        self._listener = None
        self._ping_server = None
        self._ping_task = None
        self._password_waiter = None
        self._selected_beacon = None
        self._last_join_mode = "auto"
        self._last_join_key = ""
        self._blocked = {}
        self._roster = []
        self._identity = self._identity_for("host")
        self._client_identity = self._identity_for("client")
        self._closed = False

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _identity_for(self, label):
        path = APP_DIR / (label + "-identity.json")
        value = _load_json(path, {})
        if isinstance(value.get("private"), str) and isinstance(value.get("public"), str):
            return value["private"], value["public"]
        private, public = generate_keypair()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"private": private, "public": public}), encoding="utf-8")
        return private, public

    def _emit(self, **values):
        self._patch.emit(values)

    @Slot(object)
    def _apply(self, values):
        self._state.update(values)
        self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self._state

    def _submit(self, coroutine, *, room_action=False):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        def completed(done):
            try:
                done.result()
            except Exception as exc:
                APP_DIR.mkdir(parents=True, exist_ok=True)
                (APP_DIR / "last-error.txt").write_text(
                    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    encoding="utf-8",
                )
                values = {"busy": False, "status": str(exc)}
                if room_action:
                    values["roomError"] = True
                self._emit(**values)
        future.add_done_callback(completed)
        return future

    def _save_settings(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")

    @Slot(str)
    def show(self, screen):
        if screen in ("start", "host", "join", "options"):
            self._apply({"screen": screen})

    @Slot(str)
    def copyText(self, value):
        QApplication.clipboard().setText(value)

    @Slot(str)
    def saveName(self, value):
        name = "".join(c for c in value.strip()[:32] if c.isprintable()) or self._pc_name
        self._settings["name"] = name
        self._save_settings()
        self._apply({"name": name})
        self._refresh_live_profile()

    def _refresh_live_profile(self):
        name, avatar = self._settings["name"], self._settings["avatar"]
        if self._presence_server:
            self._host_roster(None, self._roster)
            self._submit(self._presence_server.set_host_profile(name, avatar))
        if self._presence_client:
            self._presence_client.name = name
            self._presence_client.avatar = avatar
            player_id = self._client_identity[1][:64]
            self._roster = [{**player, "name": name, "avatar": avatar}
                            if player.get("player_id") == player_id else player
                            for player in self._roster]
            self._client_roster(self._roster)
            self._submit(self._presence_client.push_profile())

    @Slot(bool)
    def setWipeOnExit(self, enabled):
        self._settings["wipe_on_exit"] = enabled
        self._save_settings()
        self._apply({"wipeOnExit": enabled})

    @Slot(result=str)
    def chooseAvatar(self):
        path, _ = QFileDialog.getOpenFileName(None, "Choose profile picture", "",
                                              "Images (*.png *.jpg *.jpeg *.webp)")
        return QUrl.fromLocalFile(path).toString() if path else ""

    @Slot(str, float, float, float, result=str)
    def setAvatarCrop(self, url, zoom, horizontal, vertical):
        """Save the square previewed in the NNLL-style crop editor."""
        try:
            path = QUrl(url).toLocalFile()
            if not path:
                raise ValueError("Choose a picture first.")
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                raise ValueError("That picture could not be read.")
            zoom = max(1.0, min(3.0, float(zoom)))
            horizontal = max(0.0, min(1.0, float(horizontal)))
            vertical = max(0.0, min(1.0, float(vertical)))
            side = max(1, int(min(image.width(), image.height()) / zoom))
            left = round((image.width() - side) * horizontal)
            top = round((image.height() - side) * vertical)
            cropped = image.copy(left, top, side, side)
            thumb = cropped.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            buffer = QBuffer()
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not thumb.save(buffer, "JPG", 80):
                raise ValueError("The cropped picture could not be saved.")
            avatar = "data:image/jpeg;base64," + base64.b64encode(bytes(buffer.data())).decode("ascii")
            if len(avatar) > 16000:
                raise ValueError("Choose a smaller picture.")
            self._settings["avatar"] = avatar
            self._save_settings()
            self._apply({"avatar": avatar})
            self._refresh_live_profile()
            return ""
        except (OSError, ValueError) as exc:
            return str(exc)

    @Slot(str)
    def openRoom(self, password):
        if self._state["busy"] or self._state["connected"]:
            return
        self._apply({"busy": True, "role": "host", "roomError": False,
                     "status": "Opening room…"})
        self._submit(self._open_room(password), room_action=True)

    async def _open_room(self, password):
        password = password.strip()
        if len(password.encode("utf-8")) > 256:
            raise ValueError("Password must be at most 256 UTF-8 bytes.")
        self._tunnel.approval_policy = lambda node: node not in self._blocked
        room = await asyncio.to_thread(self._tunnel.open_room, self._pc_name,
                                       protected=bool(password))
        self._room = room
        try:
            status = await asyncio.to_thread(self._tunnel.wait_connected, room.network_id, 60)
            if not status.connected:
                raise RuntimeError(status.message)
            if password:
                self._admission = RoomAdmission(password, on_change=self._admission_changed,
                                                overlay=self._tunnel.room_overlay)
                await self._admission.start()
            self._presence_server = PresenceServer(on_change=self._host_roster,
                                                    admission=self._admission,
                                                    host_name=self._settings["name"],
                                                    host_avatar=self._settings["avatar"])
            await self._presence_server.start()
            self._host_room = HostRoom(self._identity[0], APP_DIR / "zt", APP_DIR / "room",
                                       room.network_id, _lan_ip, __version__,
                                       host_zt=room.host_ip, host_pub=self._identity[1],
                                       pc_name=self._pc_name, admission=self._admission,
                                       presence_port=self._presence_server.port)
            await self._host_room.start()
            await self._start_ping()
            self._emit(busy=False, connected=True, roomError=False, status="Room open",
                       roomKey=format_room_code(room.network_id), players=[])
        except Exception:
            await self._stop_services()
            await asyncio.to_thread(self._tunnel.close_room)
            self._room = None
            raise

    def _admission_changed(self, snapshot):
        self._emit(approval=(snapshot.get("pending") or [None])[0],
                   blacklist=snapshot.get("blacklist", []))

    def _host_roster(self, _counts, players):
        self._roster = players
        host = {"player_id": "host", "name": self._settings["name"],
                "avatar": self._settings["avatar"],
                "ip": self._room.host_ip if self._room else "", "ping_ms": 0,
                "is_host": True}
        self._emit(players=[host, *players])

    @Slot()
    def findHost(self):
        if self._state["busy"] or self._state["connected"]:
            return
        self._last_join_mode = "auto"
        self._apply({"busy": True, "role": "join", "roomError": False,
                     "status": "Searching for LAN hosts…",
                     "hostChoices": []})
        self._submit(self._find_host(), room_action=True)

    async def _find_host(self):
        found = {}
        def seen(beacon):
            found[(beacon.get("host_pub"), beacon.get("net_id"))] = beacon
        listener = await BeaconListener.listen_discovered(seen)
        try:
            await asyncio.sleep(3)
        finally:
            listener.close()
        if not found:
            raise RuntimeError("No LAN host found. Try a room key.")
        choices = list(found.values())
        if len(choices) > 1:
            self._choices = choices
            self._emit(busy=False, hostChoices=[{"index": i, "name": b.get("pc_name", "Host"),
                                                   "key": format_room_code(b["net_id"])}
                                                  for i, b in enumerate(choices)],
                       status="Choose a host")
            return
        await self._join_beacon(choices[0], already_joined=False)

    @Slot(int)
    def chooseHost(self, index):
        choices = getattr(self, "_choices", [])
        if 0 <= index < len(choices):
            self._apply({"busy": True, "roomError": False, "hostChoices": []})
            self._submit(self._join_beacon(choices[index], already_joined=False),
                         room_action=True)

    @Slot(str)
    def joinKey(self, text):
        if self._state["busy"] or self._state["connected"]:
            return
        try:
            network_id = parse_room_code(text)
        except Exception as exc:
            self._apply({"status": str(exc), "roomError": True})
            return
        self._last_join_mode = "key"
        self._last_join_key = text
        self._apply({"busy": True, "role": "join", "roomError": False,
                     "status": "Joining room key…"})
        self._submit(self._join_key(network_id), room_action=True)

    @Slot()
    def retryJoin(self):
        if self._last_join_mode == "key" and self._last_join_key:
            self.joinKey(self._last_join_key)
        else:
            self.findHost()

    async def _join_key(self, network_id):
        try:
            await asyncio.to_thread(self._tunnel.begin_join, network_id)
            status = await asyncio.to_thread(self._tunnel.wait_connected, network_id, 60)
            if not status.connected:
                raise RuntimeError(status.message)
            beacon = await fetch_room_when_ready(status.host_ip, timeout=30)
            if beacon.get("net_id") != network_id:
                raise RuntimeError("The Host returned a different room key.")
            await self._join_beacon({**beacon, "host": status.host_ip}, already_joined=True)
        except Exception:
            await asyncio.to_thread(self._tunnel.leave)
            raise

    async def _join_beacon(self, beacon, *, already_joined):
        from lantool.netcore.compat import check, proto_of
        from lantool._version import __proto__
        compatible, _, message, _ = check(beacon.get("version"), proto_of(beacon),
                                           __version__, __proto__)
        if not compatible:
            raise RuntimeError(message)
        try:
            if not already_joined:
                await asyncio.to_thread(self._tunnel.begin_join, beacon["net_id"])
                status = await asyncio.to_thread(self._tunnel.wait_connected, beacon["net_id"], 60)
                if not status.connected:
                    raise RuntimeError(status.message)
            else:
                status = await asyncio.to_thread(self._tunnel.status, beacon["net_id"])
            access = None
            if beacon.get("admission"):
                overlay_beacon = {**beacon, "host": status.host_ip}
                access = await self._admit(overlay_beacon)
                result = await request_admission(overlay_beacon, self._client_identity[1][:64],
                                                 self._settings["name"], identity=self._client_identity,
                                                 action="bind", token=access.token)
                if result.get("status") != "allowed":
                    raise RuntimeError("Host could not activate protected room access.")
            self._room = beacon
            self._presence_client = PresenceClient(self._client_identity[1][:64],
                                                    self._settings["name"], status.host_ip,
                                                    port=beacon.get("presence_port", 47800),
                                                    on_players=self._client_roster,
                                                    avatar=self._settings["avatar"], access=access,
                                                    on_kicked=lambda: asyncio.create_task(self._kicked()))
            self._presence_client.start()
            await self._presence_client.subscribe_players()
            await asyncio.wait_for(self._presence_client.connected.wait(), 10)
            await self._start_ping()
            self._emit(busy=False, connected=True, roomError=False,
                       status="Connected to room",
                       roomKey=format_room_code(beacon["net_id"]))
        except Exception:
            await self._stop_services()
            await asyncio.to_thread(self._tunnel.leave)
            self._room = None
            raise

    async def _admit(self, beacon):
        password = None
        request_id = None
        action = "join"
        while True:
            result = await request_admission(beacon, self._client_identity[1][:64],
                                             self._settings["name"], identity=self._client_identity,
                                             password=password, request_id=request_id, action=action)
            state = result.get("status")
            if state == "allowed":
                self._emit(passwordPrompt=False)
                return AdmissionAccess.from_result(beacon, result)
            if state == "pending":
                self._emit(status="Waiting for Host approval…", passwordPrompt=False)
                request_id, action = result.get("request_id"), "poll"
                await asyncio.sleep(1.5)
                continue
            if state in ("password_required", "wrong_password", "rate_limited"):
                self._emit(passwordPrompt=True, status={"password_required": "Enter room password",
                                                        "wrong_password": "Wrong password; try again",
                                                        "rate_limited": "Wait before trying again"}[state])
                self._password_waiter = self._loop.create_future()
                password = await asyncio.wait_for(self._password_waiter, 120)
                action = "join"
                continue
            raise RuntimeError("Host denied room access: " + str(state))

    @Slot(str)
    def submitPassword(self, password):
        waiter = self._password_waiter
        if waiter and not waiter.done():
            self._loop.call_soon_threadsafe(waiter.set_result, password)
            self._apply({"passwordPrompt": False})

    def _client_roster(self, players):
        self._roster = players
        host_ip = self._room.get("host_zt", "") if isinstance(self._room, dict) else ""
        host_profile = self._presence_client.host_profile if self._presence_client else {}
        host = {"player_id": "host",
                "name": host_profile.get("name") or (self._room.get("pc_name", "Host") if isinstance(self._room, dict) else "Host"),
                "avatar": host_profile.get("avatar", ""),
                "ip": host_ip, "ping_ms": None, "is_host": True}
        self._emit(players=[host, *players])

    async def _kicked(self):
        await self._leave()
        self._emit(status="Host removed you from the room", roomError=True)

    @Slot()
    def leaveRoom(self):
        if self._state["busy"]:
            return
        self._apply({"busy": True, "roomError": False, "status": "Leaving room…"})
        self._submit(self._leave(), room_action=True)

    async def _leave(self):
        await self._stop_services()
        if self._tunnel.hosting:
            await asyncio.to_thread(self._tunnel.close_room)
        else:
            await asyncio.to_thread(self._tunnel.leave)
        self._room = None
        self._blocked.clear()
        self._roster = []
        self._emit(busy=False, connected=False, roomError=False, roomKey="", players=[],
                   blacklist=[], approval=None, status="Room closed")

    async def _stop_services(self):
        if self._ping_task:
            self._ping_task.cancel()
            self._ping_task = None
        if self._ping_server:
            self._ping_server.close()
            await self._ping_server.wait_closed()
            self._ping_server = None
        if self._presence_client:
            await self._presence_client.stop()
            self._presence_client = None
        if self._host_room:
            await self._host_room.stop()
            self._host_room = None
        if self._presence_server:
            await self._presence_server.stop()
            self._presence_server = None
        if self._admission:
            await self._admission.stop()
            self._admission = None

    @Slot(str, bool)
    def kick(self, player_id, block):
        if not self._presence_server or not self._tunnel.hosting:
            return
        self._submit(self._kick(player_id, block))

    async def _kick(self, player_id, block):
        player = next((p for p in self._roster if p.get("player_id") == player_id), None)
        if not player:
            return
        if block:
            ip = player.get("ip", "")
            if self._admission:
                self._admission.block_active(player_id, player.get("name", "Player"), ip)
            else:
                node = await asyncio.to_thread(self._tunnel.room_overlay.node_for_ip, ip) if self._tunnel.room_overlay else ""
                if not node:
                    node = await asyncio.to_thread(self._node_for_ip, ip)
                if node:
                    self._blocked[node] = {"id": node, "name": player.get("name", "Player"), "ip": ip}
                    await asyncio.to_thread(self._tunnel.local._call, "POST",
                                            f"/controller/network/{self._tunnel.room.network_id}/member/{node}",
                                            {"authorized": False})
                    self._emit(blacklist=list(self._blocked.values()))
        await self._presence_server.kick(player_id)

    def _node_for_ip(self, ip):
        for member in self._tunnel.local.controller_members(self._tunnel.room.network_id):
            addresses = (member.get("config") or {}).get("ipAssignments", [])
            if any(str(address).split("/")[0] == ip for address in addresses):
                return str(member.get("nodeId") or "")
        return ""

    @Slot(str)
    def unblock(self, entry_id):
        if self._admission:
            self._loop.call_soon_threadsafe(self._admission.remove, entry_id)
        elif entry_id in self._blocked:
            self._blocked.pop(entry_id)
            self._apply({"blacklist": list(self._blocked.values())})

    @Slot(str, bool)
    def decide(self, request_id, allow):
        if self._admission:
            self._loop.call_soon_threadsafe(self._admission.decide, request_id, allow)

    async def _start_ping(self):
        try:
            self._ping_server = await asyncio.start_server(self._ping_reply, "0.0.0.0", PORT_PING)
        except OSError:
            return
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_reply(self, reader, writer):
        try:
            payload = await asyncio.wait_for(reader.readexactly(8), 2)
            writer.write(payload)
            await writer.drain()
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _ping_loop(self):
        while True:
            await asyncio.sleep(5)
            players = [dict(p) for p in self._roster]
            if isinstance(self._room, dict):
                players.insert(0, {"player_id": "host", "name": self._room.get("pc_name", "Host"),
                                   "ip": self._room.get("host_zt", ""), "avatar": "",
                                   "is_host": True, "ping_ms": None})
            elif self._room:
                players.insert(0, {"player_id": "host", "name": self._settings["name"],
                                   "ip": self._room.host_ip, "avatar": self._settings["avatar"],
                                   "is_host": True, "ping_ms": 0})
            for player in players:
                ip = player.get("ip")
                if not ip or (self._tunnel.hosting and player.get("is_host")):
                    continue
                stamp = time.monotonic()
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, PORT_PING), 2)
                    nonce = secrets.token_bytes(8)
                    writer.write(nonce)
                    await writer.drain()
                    reply = await asyncio.wait_for(reader.readexactly(8), 2)
                    writer.close()
                    player["ping_ms"] = round((time.monotonic() - stamp) * 1000) if reply == nonce else None
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                    player["ping_ms"] = None
            self._emit(players=players)

    @Slot()
    def runDiagnostics(self):
        self._apply({"diagnostic": [{"title": "Checking network", "detail": "Please wait…"}]})
        self._submit(self._diagnose())

    async def _diagnose(self):
        from silent_tunnel import installer
        rows = []
        installed = await asyncio.to_thread(installer.is_installed)
        rows.append({"title": "VLA service", "detail": "Installed and package verified" if installed else "Missing or not owned by the NNLL backend"})
        try:
            service = await asyncio.to_thread(self._tunnel.local.status)
            rows.append({"title": "Local VLA API", "detail": "Responding · " + str(service.get("address", "no node ID"))})
        except Exception as exc:
            rows.append({"title": "Local VLA API", "detail": str(exc)})
        rows.append({"title": "LAN address", "detail": _lan_ip() or "No LAN address found"})
        rows.append({"title": "Room", "detail": self._state["roomKey"] or "No active room"})
        if self._room:
            network_id = self._room.network_id if hasattr(self._room, "network_id") else self._room["net_id"]
            status = await asyncio.to_thread(self._tunnel.status, network_id)
            rows.append({"title": "VLA adapter", "detail": status.message})
            if isinstance(self._room, dict):
                host_ip = self._room.get("host_zt", "")
                if host_ip:
                    try:
                        reader, writer = await asyncio.wait_for(asyncio.open_connection(host_ip, 47802), 3)
                        writer.close()
                        rows.append({"title": "Host metadata port", "detail": host_ip + ":47802 reachable"})
                    except (OSError, asyncio.TimeoutError):
                        rows.append({"title": "Host metadata port", "detail": host_ip + ":47802 unreachable"})
        rows.append({"title": "Peer ping", "detail": "TCP 47808 on the VLA IP; a dash means the peer did not answer."})
        self._emit(diagnostic=rows)

    def close(self):
        if self._closed:
            return
        self._closed = True
        future = asyncio.run_coroutine_threadsafe(self._leave(), self._loop)
        try:
            future.result(timeout=20)
        except Exception:
            pass
        # Service, adapter, and driver removal is intentionally not attempted from
        # this development build: the copied NNLL backend is not owned by this app.
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
