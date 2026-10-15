import os
import queue
import secrets
import sys

from .config import Config, app_dir
from .store import Store


def already_running():
    """Single-instance guard via a named Win32 mutex (no-op elsewhere)."""
    if sys.platform != "win32":
        return False
    import ctypes

    kernel32 = ctypes.windll.kernel32
    global _mutex  # keep the handle alive for the process lifetime
    _mutex = kernel32.CreateMutexW(None, False, "Local\\MiniBroSingleInstance")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def start_agent_server(events):
    """Create the shared secret and start the pipe server. The island still works if this fails."""
    from .server import Server

    token_path = os.path.join(app_dir(), "token")
    token = secrets.token_hex(16)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass
    try:
        Server(token, events).start()
    except OSError:
        pass


def refresh_installed_hook():
    """If any agent is connected, re-copy the relay script so app updates reach it."""
    from . import agents

    for agent in agents.SPECS:
        try:
            data, _ = agents.read_settings(agents.settings_path(agent))
            if agents.is_connected(data):
                agents.hook_target()
                return
        except (OSError, ValueError):
            continue


def main():
    if already_running():
        return
    from .agents import Tracker
    from .island import Island  # imported late so tests/imports don't need tkinterdnd2

    events = queue.Queue()
    start_agent_server(events)
    refresh_installed_hook()
    store = Store(os.path.join(app_dir(), "shelf"))
    Island(store, Config(), events, Tracker()).run()


if __name__ == "__main__":
    main()
