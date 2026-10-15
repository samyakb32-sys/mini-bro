"""Hook relay: run by a coding agent for each hook event. Standalone (stdlib only) so it starts fast.

Reads the event JSON on stdin, forwards a trimmed copy to the Mini-Bro island over a local pipe, and
for PermissionRequest waits for the user's Allow/Deny and prints the agent's documented reply.
It never blocks the agent: no island running (or no answer) means no output, so the agent just asks
in its terminal as usual.
"""
import json
import os
import sys
import time
from multiprocessing.connection import Client

CONNECT_WINDOW = 0.3  # seconds to find the island before giving up
WAIT = 110  # seconds to wait for a decision; the app answers first (108 s), the agent hook limit is 120 s
DROP_KEYS = {"tool_response", "transcript_path"}
STR_LIMIT = 2048
EDIT_LIMIT = 256 * 1024
AGENTS = {"claude": "Claude Code"}


def app_dir():
    base = os.environ.get("MINIBRO_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share"), "MiniBro")
    return base


def address():
    if os.environ.get("MINIBRO_ADDR"):
        return os.environ["MINIBRO_ADDR"]
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return r"\\.\pipe\minibro-" + user


def trim(value, limit=STR_LIMIT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [trim(v, limit) for v in value[:50]]
    if isinstance(value, dict):
        return {k: trim(v, EDIT_LIMIT if k in ("new_string", "old_string", "content", "diff") else limit)
                for k, v in value.items() if k not in DROP_KEYS}
    return value


def first(raw, *keys):
    for k in keys:
        if raw.get(k):
            return raw[k]
    return ""


def normalize(raw, agent):
    """Map an agent's field names onto the Claude Code ones the island understands."""
    return {
        "agent": agent,
        "event": first(raw, "hook_event_name", "hookEventName", "event"),
        "session_id": str(first(raw, "session_id", "sessionId", "conversation_id")),
        "cwd": first(raw, "cwd", "workspace", "project_dir"),
        "tool_name": first(raw, "tool_name", "toolName"),
        "tool_input": trim(raw.get("tool_input") or raw.get("toolInput") or {}),
        "message": trim(first(raw, "message", "prompt")),
    }


def connect():
    deadline = time.time() + CONNECT_WINDOW
    while True:
        try:
            return Client(address())
        except OSError:
            if time.time() >= deadline:
                return None
            time.sleep(0.02)


def reply_for(decision):
    if decision == "allow":
        body = {"behavior": "allow"}
    elif decision == "deny":
        body = {"behavior": "deny", "message": "Denied from Mini-Bro"}
    else:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": body}}


def main(argv=None, stdin=None, stdout=None):
    argv = sys.argv[1:] if argv is None else argv
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv and argv.index("--agent") + 1 < len(argv) else "claude"
    try:
        raw = json.loads(stdin.read() or "{}")
    except ValueError:
        return 0
    msg = normalize(raw, agent)
    try:
        with open(os.path.join(app_dir(), "token"), encoding="utf-8") as f:
            msg["token"] = f.read().strip()
    except OSError:
        return 0
    conn = connect()
    if conn is None:
        return 0
    try:
        conn.send_bytes(json.dumps(msg).encode("utf-8"))
        if msg["event"] != "PermissionRequest":
            return 0
        if not conn.poll(WAIT):
            return 0
        out = reply_for(json.loads(conn.recv_bytes()).get("decision"))
        if out:
            stdout.write(json.dumps(out))
            stdout.flush()
    except (OSError, EOFError, ValueError):
        pass
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
