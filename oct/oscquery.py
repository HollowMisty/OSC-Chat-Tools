"""Minimal OSCQuery service for VRChat coexistence.

The classic VRChat OSC setup has one app bound to UDP 9001, so a second listener
can't share it ("Only one usage of each socket address"). OSCQuery fixes this:
we bind any free UDP port for receiving, then advertise ourselves over mDNS
(Zeroconf) plus a tiny oscjson HTTP endpoint describing the parameters we want.
VRChat discovers each registered app and sends avatar parameters to all of them
on their own ports - so OCT coexists with face tracking, other chatbox tools, etc.

This implements just enough of the OSCQuery spec (HOST_INFO + a node tree) for
VRChat discovery, and depends only on `zeroconf` (PyPI) + the standard library.
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from zeroconf import ServiceInfo, Zeroconf
    _ZC_OK = True
except Exception:  # pragma: no cover - depends on host install
    _ZC_OK = False


def available() -> bool:
    """Whether OSCQuery can run (zeroconf importable)."""
    return _ZC_OK


def _open_port(kind) -> int:
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def get_open_udp_port() -> int:
    return _open_port(socket.SOCK_DGRAM)


def get_open_tcp_port() -> int:
    return _open_port(socket.SOCK_STREAM)


class OSCQueryService:
    """Advertises an OSC receive port via mDNS + an oscjson HTTP endpoint."""

    def __init__(self, name: str, http_port: int, osc_port: int,
                 osc_ip: str = "127.0.0.1", endpoints: tuple = ()):
        self.name = name
        self.http_port = http_port
        self.osc_port = osc_port
        self.osc_ip = osc_ip
        self.endpoints = list(endpoints)
        self._zc = None
        self._http = None
        self._http_thread = None
        self._start()

    # -- oscjson payloads ----------------------------------------------------
    def _host_info(self) -> dict:
        return {
            "NAME": self.name,
            "OSC_IP": self.osc_ip,
            "OSC_PORT": self.osc_port,
            "OSC_TRANSPORT": "UDP",
            "EXTENSIONS": {"ACCESS": True, "VALUE": True, "RANGE": True,
                           "TYPE": True, "DESCRIPTION": True},
        }

    def _tree(self) -> dict:
        root = {"FULL_PATH": "/", "ACCESS": 0, "CONTENTS": {}}
        for path in self.endpoints:
            parts = [p for p in path.split("/") if p]
            node, cur = root, ""
            for i, part in enumerate(parts):
                cur += "/" + part
                contents = node.setdefault("CONTENTS", {})
                child = contents.setdefault(part, {"FULL_PATH": cur, "ACCESS": 0})
                if i == len(parts) - 1:
                    child["ACCESS"] = 3      # read/write
                    child["TYPE"] = "T"      # bool-ish; VRChat is lenient
                node = child
        return root

    def _find(self, path: str):
        tree = self._tree()
        if path in ("/", ""):
            return tree
        node = tree
        for part in [p for p in path.split("/") if p]:
            node = node.get("CONTENTS", {}).get(part)
            if node is None:
                return None
        return node

    # -- lifecycle -----------------------------------------------------------
    def _start(self):
        self._http = HTTPServer(("127.0.0.1", self.http_port), _make_handler(self))
        self._http_thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._http_thread.start()
        if not _ZC_OK:
            return
        self._zc = Zeroconf()
        addr = socket.inet_aton("127.0.0.1")
        host = self.name.replace(" ", "-") + ".local."
        self._zc.register_service(ServiceInfo(
            "_oscjson._tcp.local.", f"{self.name}._oscjson._tcp.local.",
            addresses=[addr], port=self.http_port, properties={"txtvers": "1"}, server=host))
        self._zc.register_service(ServiceInfo(
            "_osc._udp.local.", f"{self.name}._osc._udp.local.",
            addresses=[addr], port=self.osc_port, properties={"txtvers": "1"}, server=host))

    def stop(self):
        if self._zc is not None:
            try:
                self._zc.unregister_all_services()
            except Exception:
                pass
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None


def _make_handler(service: OSCQueryService):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                if "HOST_INFO" in self.path:
                    payload = service._host_info()
                else:
                    payload = service._find(self.path.split("?")[0])
                if payload is None:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"OSC path not found")
                    return
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                try:
                    self.send_response(500)
                    self.end_headers()
                except Exception:
                    pass

        def log_message(self, *args):  # silence stdout logging
            pass

    return _Handler
