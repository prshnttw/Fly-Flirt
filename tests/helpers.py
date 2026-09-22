"""Test helpers: simulated browsers (Flask cookie jar + Socket.IO client)."""
from __future__ import annotations

import time

from flyflirt import create_app, socketio
from flyflirt.config import TestConfig


def make_app(**overrides):
    cfg = type("Cfg", (TestConfig,), overrides)
    return create_app(cfg)


class Browser:
    """One person's browser: a cookie jar (so the server sees a stable client id) and a socket."""

    def __init__(self, app):
        self.app = app
        self.http = app.test_client()
        self.http.get("/healthz")  # any request issues the session cookie
        self.sio = socketio.test_client(app, flask_test_client=self.http)
        self.log: list[dict] = []

    def emit(self, name: str, data=None) -> None:
        self.sio.emit(name, data)

    def pump(self) -> None:
        self.log.extend(self.sio.get_received())

    def wait(self, name: str, timeout: float = 5.0, where=None):
        """Wait for (and consume) the next event called ``name``; returns its payload."""
        deadline = time.time() + timeout
        while True:
            self.pump()
            for i, evt in enumerate(self.log):
                if evt["name"] == name and (where is None or where(evt["args"][0])):
                    self.log.pop(i)
                    return evt["args"][0]
            if time.time() > deadline:
                raise AssertionError(f"timed out waiting for {name!r}; saw {[e['name'] for e in self.log]}")
            time.sleep(0.02)

    def has(self, name: str) -> bool:
        self.pump()
        return any(e["name"] == name for e in self.log)

    def drain(self, name: str) -> list:
        self.pump()
        out = [e["args"][0] for e in self.log if e["name"] == name]
        self.log = [e for e in self.log if e["name"] != name]
        return out

    def json(self, path: str):
        response = self.http.get(path)
        return response.status_code, response.get_json(silent=True)

    def disconnect(self) -> None:
        self.sio.disconnect()


def pair_up(app, mode_a="male", mode_b="female"):
    a, b = Browser(app), Browser(app)
    a.emit("queue_join", {"mode": mode_a, "nickname": "Ana"})
    a.wait("queue_waiting")
    b.emit("queue_join", {"mode": mode_b, "nickname": "Ben"})
    ma, mb = a.wait("matched"), b.wait("matched")
    assert ma["room_id"] == mb["room_id"]
    return a, b, ma["room_id"], ma, mb


def enter_room(browser, room_id):
    browser.emit("room_join", {"room_id": room_id})
    return browser.wait("room_state")


def send(sender, room_id, text, listeners, timeout=8.0):
    """Send a message and wait until every listener has its analysis (fly_update)."""
    sender.emit("message", {"room_id": room_id, "text": text})
    updates = []
    for listener in listeners:
        listener.wait("new_message", timeout)
        updates.append(listener.wait("fly_update", timeout))
        listener.wait("brain_wave", timeout)
    return next((u for u in updates if u.get("private")), updates[0])
