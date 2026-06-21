"""OSC I/O: send chatbox messages to VRChat, optionally listen and forward.

VRChat chatbox OSC API:
  /chatbox/input  [string, bool send_now, bool notify]
  /chatbox/typing [bool]
"""
from __future__ import annotations

import threading
from typing import Callable

from pythonosc import udp_client, osc_server
from pythonosc.dispatcher import Dispatcher

CHATBOX_INPUT = "/chatbox/input"
CHATBOX_TYPING = "/chatbox/typing"


class OSCClient:
    """Sends OSC messages to a target address (VRChat by default)."""

    def __init__(self, address: str = "127.0.0.1", port: int | str = 9000):
        self._client = udp_client.SimpleUDPClient(address, int(port))

    def send_chatbox(self, text: str, send_now: bool = True, notify: bool = False) -> None:
        self._client.send_message(CHATBOX_INPUT, [text, bool(send_now), bool(notify)])

    def send_typing(self, typing: bool) -> None:
        self._client.send_message(CHATBOX_TYPING, bool(typing))

    def send(self, address: str, value) -> None:
        self._client.send_message(address, value)


class OSCListener:
    """Listens for incoming OSC and optionally forwards every message on."""

    def __init__(
        self,
        address: str = "127.0.0.1",
        port: int | str = 9001,
        on_message: Callable[[str, tuple], None] | None = None,
        forward_client: "OSCClient | None" = None,
    ):
        self.address = address
        self.port = int(port)
        self.on_message = on_message
        self.forward_client = forward_client
        self._server = None
        self._thread: threading.Thread | None = None

    def _handler(self, addr: str, *args) -> None:
        if self.on_message:
            try:
                self.on_message(addr, args)
            except Exception:
                pass
        if self.forward_client:
            try:
                self.forward_client.send(addr, list(args) if len(args) != 1 else args[0])
            except Exception:
                pass

    def start(self) -> None:
        if self._server is not None:
            return
        dispatcher = Dispatcher()
        dispatcher.set_default_handler(self._handler)
        self._server = osc_server.ThreadingOSCUDPServer((self.address, self.port), dispatcher)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
            self._thread = None
