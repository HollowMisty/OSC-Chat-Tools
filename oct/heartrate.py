"""Heart-rate sources (Pulsoid and HypeRate) over websockets.

Each provider runs a background thread, reconnects on failure, and calls
``on_bpm(int)`` whenever a new reading arrives. Stop with ``stop()``.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from websocket import create_connection, WebSocketTimeoutException


class _BaseHRProvider:
    def __init__(self, on_bpm: Callable[[int], None]):
        self.on_bpm = on_bpm
        self._running = False
        self._thread: threading.Thread | None = None
        self._ws = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._close_ws()

    def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _url(self) -> str:
        raise NotImplementedError

    def _on_open(self, ws) -> None:
        pass

    def _parse(self, message: str) -> int | None:
        raise NotImplementedError

    def _run(self) -> None:
        while self._running:
            try:
                self._ws = create_connection(self._url(), timeout=10)
                self._ws.settimeout(1.0)  # so recv() unblocks to check _running
                self._on_open(self._ws)
                while self._running:
                    try:
                        message = self._ws.recv()
                    except WebSocketTimeoutException:
                        continue
                    if not message:
                        continue
                    bpm = self._parse(message)
                    if bpm is not None:
                        try:
                            self.on_bpm(bpm)
                        except Exception:
                            pass
            except Exception:
                if self._running:
                    time.sleep(3)  # back off before reconnecting
            finally:
                self._close_ws()


class PulsoidProvider(_BaseHRProvider):
    def __init__(self, token: str, on_bpm: Callable[[int], None]):
        super().__init__(on_bpm)
        self.token = token

    def _url(self) -> str:
        return (
            "wss://dev.pulsoid.net/api/v1/data/real_time"
            f"?access_token={self.token}&response_mode=text_plain_only_heart_rate"
        )

    def _parse(self, message: str) -> int | None:
        # text_plain mode returns the BPM as a bare number; fall back to JSON.
        try:
            return int(str(message).strip())
        except (TypeError, ValueError):
            try:
                return int(json.loads(message)["data"]["heart_rate"])
            except Exception:
                return None


class HypeRateProvider(_BaseHRProvider):
    """HypeRate uses a Phoenix-channels websocket; join hr:<session_id>."""

    def __init__(self, api_key: str, session_id: str, on_bpm: Callable[[int], None]):
        super().__init__(on_bpm)
        self.api_key = api_key
        self.session_id = session_id

    def _url(self) -> str:
        return f"wss://app.hyperate.io/socket/websocket?token={self.api_key}"

    def _on_open(self, ws) -> None:
        join = {
            "topic": f"hr:{self.session_id}",
            "event": "phx_join",
            "payload": {},
            "ref": 0,
        }
        ws.send(json.dumps(join))
        # keep-alive heartbeat so Phoenix doesn't drop the socket
        def _heartbeat():
            ref = 1
            while self._running:
                time.sleep(15)
                try:
                    ws.send(json.dumps({
                        "topic": "phoenix", "event": "heartbeat",
                        "payload": {}, "ref": ref,
                    }))
                    ref += 1
                except Exception:
                    break
        threading.Thread(target=_heartbeat, daemon=True).start()

    def _parse(self, message: str) -> int | None:
        try:
            payload = json.loads(message).get("payload") or {}
            hr = payload.get("hr")
            return int(hr) if hr is not None else None
        except Exception:
            return None
