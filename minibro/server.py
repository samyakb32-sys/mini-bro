"""Local pipe server. Hook relays connect here; permission requests become cards for the UI.

Runs one accept thread plus a short-lived thread per connection. The UI never blocks on it: events
are handed over through a queue.Queue and cards are answered through a thread-safe Card object.
"""
import json
import os
import queue
import threading
import time
from multiprocessing.connection import Listener

from .hook import address

SHOWN_TIMEOUT = 0.8  # decline at once if the card is not on screen by then
DECISION_TIMEOUT = 108  # shorter than the relay's 110 s and the agent's 120 s
NO_CARD_TOOLS = {"AskUserQuestion"}  # needs an answer card, not Allow/Deny: leave it to the terminal


class Card:
    def __init__(self, msg):
        self.agent = msg.get("agent", "")
        self.session_id = msg.get("session_id", "")
        self.cwd = msg.get("cwd", "")
        self.tool = msg.get("tool_name", "")
        self.tool_input = msg.get("tool_input") or {}
        self.shown = threading.Event()
        self.done = threading.Event()
        self.decision = None
        self.created = time.time()

    def finish(self, decision):
        if not self.done.is_set():
            self.decision = decision
            self.done.set()


class Server:
    def __init__(self, token, events, addr=None, shown_timeout=SHOWN_TIMEOUT, decision_timeout=DECISION_TIMEOUT):
        self.token = token
        self.events = events  # queue.Queue of ("event", msg) / ("card", Card)
        self.addr = addr or address()
        self.shown_timeout = shown_timeout
        self.decision_timeout = decision_timeout
        self.listener = None

    def start(self):
        if not self.addr.startswith("\\\\") and os.path.exists(self.addr):
            os.remove(self.addr)  # stale unix socket from a crashed run (non-Windows dev only)
        self.listener = Listener(self.addr)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def close(self):
        if self.listener:
            self.listener.close()

    def _accept_loop(self):
        while True:
            try:
                conn = self.listener.accept()
            except (OSError, EOFError):
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            if not conn.poll(5):  # client has 5 s to send its request
                return
            msg = json.loads(conn.recv_bytes(1 << 20))
            if msg.get("token") != self.token:
                return
            if msg.get("event") != "PermissionRequest":
                self.events.put(("event", msg))
                return
            if msg.get("tool_name") in NO_CARD_TOOLS:
                self._reply(conn, "decline")
                return
            card = Card(msg)
            self.events.put(("card", card))
            if not card.shown.wait(self.shown_timeout):
                card.finish("decline")
            elif not card.done.wait(self.decision_timeout):
                card.finish("decline")
            self._reply(conn, card.decision)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            conn.close()

    @staticmethod
    def _reply(conn, decision):
        conn.send_bytes(json.dumps({"decision": decision}).encode("utf-8"))
