"""Spotify Web API integration: PKCE link flow, token refresh, now-playing.

The link flow opens the Spotify authorization page and runs a tiny local server
on 127.0.0.1:8000 to receive the redirect (via the project's redirect page),
exactly like the original. ``SpotifyClient`` then fetches the current playback,
auto-refreshing the access token when it expires.
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
import webbrowser
from typing import Callable

import requests

REDIRECT_URI = "https://lioncat6.github.io/redirect"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPE = "user-read-playback-state user-read-currently-playing"


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("utf-8").rstrip("=")
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").replace("=", "")
    return verifier, challenge


def link(client_id: str, on_log: Callable[[str], None] = lambda m: None):
    """Run the PKCE authorization flow (blocking).

    Returns (access_token, refresh_token, display_name) or raises on failure.
    """
    from flask import Flask, request
    from werkzeug.serving import make_server

    verifier, challenge = _pkce()
    params = {
        "client_id": client_id, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    auth_url = requests.Request("GET", AUTH_URL, params=params).prepare().url
    result: dict = {}
    app = Flask(__name__)
    server = make_server("127.0.0.1", 8000, app)

    @app.route("/callback")
    def callback():
        def shutdown():
            server.shutdown()
        if "error" in request.args:
            result["error"] = request.args.get("error")
            threading.Thread(target=shutdown, daemon=True).start()
            return "Authorization failed. You can close this tab."
        try:
            access, refresh, name = _exchange_and_verify(client_id, request.args.get("code"), verifier)
            result["access"], result["refresh"], result["name"] = access, refresh, name
            threading.Thread(target=shutdown, daemon=True).start()
            return "Authorization successful. You can close this tab and return to OCT."
        except Exception as e:
            result["error"] = str(e)
            threading.Thread(target=shutdown, daemon=True).start()
            return f"Authorization failed: {e}"

    on_log("Opening Spotify authorization in your browser...")
    webbrowser.open_new(auth_url)
    try:
        server.serve_forever()  # blocks until callback shuts it down
    finally:
        try:
            server.server_close()
        except Exception:
            pass
    if "error" in result:
        raise Exception(result["error"])
    if "access" not in result:
        raise Exception("No authorization received")
    return result["access"], result["refresh"], result.get("name") or ""


def refresh_token(client_id: str, refresh: str) -> tuple[str, str]:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
    })
    if resp.status_code != 200:
        raise Exception(f"refresh error: {resp.text}")
    tok = resp.json()
    return tok.get("access_token"), (tok.get("refresh_token") or refresh)


def _exchange_and_verify(client_id, code, verifier):
    """Swap an auth code for tokens and verify the token works. Returns
    (access, refresh, name) or raises (incl. 403 for non-allowlisted accounts)."""
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI, "client_id": client_id, "code_verifier": verifier,
    })
    if resp.status_code != 200:
        raise Exception(f"token error {resp.status_code}: {resp.text}")
    tok = resp.json()
    access, refresh = tok.get("access_token"), tok.get("refresh_token")
    prof = requests.get("https://api.spotify.com/v1/me",
                        headers={"Authorization": "Bearer " + access})
    if not prof.ok:
        if prof.status_code == 403:
            raise Exception(
                "this account isn't registered for the Spotify app (403). "
                "Use your own Client ID - see the ? guide.")
        raise Exception(f"verification failed: HTTP {prof.status_code}")
    pj = prof.json()
    return access, refresh, (pj.get("display_name") or pj.get("id") or "")


def build_manual_auth(client_id):
    """For the manual fallback: return (verifier, auth_url) without a local server."""
    verifier, challenge = _pkce()
    params = {
        "client_id": client_id, "response_type": "code", "scope": SCOPE,
        "redirect_uri": REDIRECT_URI, "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    return verifier, requests.Request("GET", AUTH_URL, params=params).prepare().url


def exchange_code(client_id, code, verifier):
    """Manual fallback: exchange a pasted auth code. Returns (access, refresh, name)."""
    return _exchange_and_verify(client_id, code, verifier)


class SpotifyClient:
    def __init__(self, client_id, access, refresh, on_tokens=None):
        self.client_id = client_id
        self.access = access
        self.refresh = refresh
        self.on_tokens = on_tokens  # callback(access, refresh) to persist new tokens
        self.last_error = ""

    def _get_playstate(self):
        headers = {"Authorization": "Bearer " + (self.access or "")}
        resp = requests.get("https://api.spotify.com/v1/me/player", headers=headers, timeout=8)
        if resp.status_code == 401:
            self.access, self.refresh = refresh_token(self.client_id, self.refresh)
            if self.on_tokens:
                self.on_tokens(self.access, self.refresh)
            resp = requests.get("https://api.spotify.com/v1/me/player",
                                headers={"Authorization": "Bearer " + self.access}, timeout=8)
        if resp.status_code == 204:
            self.last_error = "nothing playing / no active device (204)"
            return None
        if not resp.ok:
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:140]}"
            return None
        self.last_error = ""
        return resp.json()

    def now_playing(self):
        try:
            ps = self._get_playstate()
        except Exception as e:
            self.last_error = str(e)
            return None
        if not ps or not ps.get("item"):
            if not self.last_error:
                self.last_error = "no track in playback state"
            return None
        item = ps["item"]
        artists = item.get("artists") or [{}]
        return {
            "title": item.get("name", ""),
            "artist": artists[0].get("name", ""),
            "album_title": (item.get("album") or {}).get("name", ""),
            "album_artist": artists[0].get("name", ""),
            "song_progress": _fmt_time((ps.get("progress_ms") or 0) / 1000),
            "song_length": _fmt_time((item.get("duration_ms") or 0) / 1000),
            "progress_ms": ps.get("progress_ms") or 0,
            "duration_ms": item.get("duration_ms") or 0,
            "volume": str((ps.get("device") or {}).get("volume_percent", 0)),
            "song_id": item.get("id", ""),
            "url": (item.get("external_urls") or {}).get("spotify", ""),
            "playing": bool(ps.get("is_playing")),
        }
