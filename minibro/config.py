import json
import os
import sys
import tempfile

DEFAULTS = {"expiry_minutes": 60, "x": None, "y": None}


def app_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, "MiniBro")
    os.makedirs(path, exist_ok=True)
    return path


def write_json_atomic(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(app_dir(), "config.json")
        self.data = dict(DEFAULTS)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data[key]

    def set(self, **kwargs):
        self.data.update(kwargs)
        write_json_atomic(self.path, self.data)


def launch_command():
    """Command line used to start the app at login."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[: -len("python.exe")] + "pythonw.exe"
    return f'"{exe}" -m minibro.main'
