import os
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


def main():
    if already_running():
        return
    from .island import Island  # imported late so tests/imports don't need tkinterdnd2

    store = Store(os.path.join(app_dir(), "shelf"))
    Island(store, Config()).run()


if __name__ == "__main__":
    main()
