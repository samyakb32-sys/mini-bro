"""Agent bookkeeping and hook installation. No UI, stdlib only."""
import copy
import difflib
import json
import os
import time

from .config import write_json_atomic

MARKER = "minibro-hook"  # present in every command we install, so we can find and remove our entries
NAMES = {"claude": "Claude Code"}
# event -> (needs matcher, timeout seconds)
HOOK_EVENTS = {
    "SessionStart": (False, 5),
    "SessionEnd": (False, 5),
    "UserPromptSubmit": (False, 5),
    "PermissionRequest": (True, 120),
    "Notification": (False, 5),
    "Stop": (False, 5),
}
SESSION_TTL = 12 * 3600


def agent_name(agent):
    return NAMES.get(agent, agent.title())


def project_name(cwd):
    return (cwd or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "(no folder)"


def summarize_tool(tool, tool_input, limit=160):
    """One-line description of what the agent wants to do."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    detail = (ti.get("command") or ti.get("file_path") or ti.get("path") or ti.get("url")
              or ti.get("pattern") or ti.get("description") or "")
    if not detail and ti:
        detail = json.dumps(ti, ensure_ascii=False)
    detail = " ".join(str(detail).split())
    text = f"{tool}  {detail}".strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class Tracker:
    """Live view of agent sessions, fed by hook events."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.sessions = {}

    def update(self, msg):
        key = (msg.get("agent", ""), msg.get("session_id", ""))
        event = msg.get("event")
        if event == "SessionEnd":
            self.sessions.pop(key, None)
            return
        status = {"UserPromptSubmit": "working", "PermissionRequest": "waiting",
                  "Notification": "waiting", "Stop": "idle", "SessionStart": "idle"}.get(event)
        if status is None:
            return
        s = self.sessions.setdefault(key, {"agent": key[0], "session_id": key[1], "cwd": "", "status": "idle"})
        s["status"] = status
        s["cwd"] = msg.get("cwd") or s["cwd"]
        s["updated"] = self.clock()

    def set_status(self, agent, session_id, status):
        s = self.sessions.get((agent, session_id))
        if s:
            s["status"] = status
            s["updated"] = self.clock()

    def prune(self):
        cutoff = self.clock() - SESSION_TTL
        for key in [k for k, s in self.sessions.items() if s.get("updated", 0) < cutoff]:
            del self.sessions[key]

    def list(self):
        order = {"waiting": 0, "working": 1, "idle": 2}
        return sorted(self.sessions.values(), key=lambda s: (order[s["status"]], -s.get("updated", 0)))


# --- Claude Code settings.json ------------------------------------------------------------------

def claude_settings_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def hook_command(python_exe, hook_path, agent="claude"):
    """Claude Code on Windows runs hooks through Git Bash, so use forward slashes."""
    py, hp = python_exe.replace("\\", "/"), hook_path.replace("\\", "/")
    if hp.lower().endswith(".exe"):
        return f'"{hp}" --agent {agent}'
    return f'"{py}" -I -S "{hp}" --agent {agent}'


def _is_ours(entry):
    return any(MARKER in h.get("command", "") for h in entry.get("hooks", []))


def remove_hooks(settings):
    out = copy.deepcopy(settings)
    hooks = out.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [e for e in hooks[event] if not _is_ours(e)]
        if not hooks[event]:
            del hooks[event]
    if "hooks" in out and not out["hooks"]:
        del out["hooks"]
    return out


def merge_hooks(settings, command):
    out = remove_hooks(settings)
    hooks = out.setdefault("hooks", {})
    for event, (matcher, timeout) in HOOK_EVENTS.items():
        entry = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            entry["matcher"] = "*"
        hooks.setdefault(event, []).append(entry)
    return out


def is_connected(settings):
    return any(_is_ours(e) for entries in settings.get("hooks", {}).values() for e in entries)


def read_settings(path):
    """Return (settings dict, original text). Raises ValueError if the file is not valid JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, ""
    if not text.strip():
        return {}, text
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data, text


def render(settings):
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def diff(old_text, new_settings):
    return "".join(difflib.unified_diff(old_text.splitlines(True), render(new_settings).splitlines(True),
                                        "settings.json (current)", "settings.json (new)"))


def apply_settings(path, new_settings):
    """Write settings with a dated backup of the previous file. Returns the backup path or None."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backup = None
    if os.path.exists(path):
        backup = f"{path}.minibro-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        with open(path, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
    write_json_atomic(path, new_settings, indent=2)
    return backup


def hook_target():
    """(python_exe, hook_path) for the command to install. The script is copied to the app folder
    under a name containing MARKER, so the installed command is recognisable and survives moving the repo."""
    import shutil
    import sys

    from .config import app_dir

    if getattr(sys, "frozen", False):
        return "", os.path.join(os.path.dirname(sys.executable), "minibro-hook.exe")
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[: -len("pythonw.exe")] + "python.exe"
    dest = os.path.join(app_dir(), MARKER + ".py")
    shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py"), dest)
    return exe, dest
