import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from minibro import agents
from minibro.server import Server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "minibro", "hook.py")


class SettingsTests(unittest.TestCase):
    CMD = '"python" -I -S "C:/x/minibro-hook.py" --agent claude'

    def test_merge_preserves_existing_and_is_idempotent(self):
        base = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
        once = agents.merge_hooks(base, self.CMD)
        twice = agents.merge_hooks(once, self.CMD)
        self.assertEqual(once, twice)
        self.assertEqual(once["model"], "x")
        self.assertEqual(len(once["hooks"]["Stop"]), 2)
        self.assertEqual(once["hooks"]["Stop"][0]["hooks"][0]["command"], "mine")
        self.assertEqual(once["hooks"]["PermissionRequest"][0]["matcher"], "*")
        self.assertEqual(once["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 120)
        self.assertTrue(agents.is_connected(once))

    def test_remove_restores_original(self):
        base = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
        self.assertEqual(agents.remove_hooks(agents.merge_hooks(base, self.CMD)), base)
        self.assertEqual(agents.remove_hooks(agents.merge_hooks({}, self.CMD)), {})
        self.assertFalse(agents.is_connected(base))

    def test_command_uses_forward_slashes(self):
        cmd = agents.hook_command(r"C:\Python\python.exe", r"C:\Users\me\AppData\Local\MiniBro\minibro-hook.py")
        self.assertNotIn("\\", cmd)
        self.assertIn(agents.MARKER, cmd)
        self.assertIn("minibro-hook.exe", agents.hook_command("", r"C:\a\minibro-hook.exe"))

    def test_apply_backs_up_and_diff_shows_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".claude", "settings.json")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write('{"model": "x"}')
            data, text = agents.read_settings(path)
            new = agents.merge_hooks(data, self.CMD)
            self.assertIn("+", agents.diff(text, new))
            backup = agents.apply_settings(path, new)
            with open(backup) as f:
                self.assertEqual(f.read(), '{"model": "x"}')
            self.assertTrue(agents.is_connected(agents.read_settings(path)[0]))

    def test_invalid_json_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(ValueError):
                agents.read_settings(path)

    def test_missing_file(self):
        self.assertEqual(agents.read_settings("/nonexistent/x.json"), ({}, ""))


class HelperTests(unittest.TestCase):
    def test_summarize(self):
        self.assertEqual(agents.summarize_tool("Bash", {"command": "npm  test"}), "Bash  npm test")
        self.assertEqual(agents.summarize_tool("Edit", {"file_path": "/a/b.py", "x": 1}), "Edit  /a/b.py")
        self.assertTrue(agents.summarize_tool("Bash", {"command": "x" * 500}).endswith("…"))
        self.assertEqual(agents.summarize_tool("Foo", {}), "Foo")

    def test_tracker(self):
        now = [1000.0]
        t = agents.Tracker(clock=lambda: now[0])
        t.update({"agent": "claude", "session_id": "a", "event": "SessionStart", "cwd": "/p/one"})
        t.update({"agent": "claude", "session_id": "b", "event": "UserPromptSubmit", "cwd": "/p/two"})
        t.update({"agent": "claude", "session_id": "b", "event": "PermissionRequest"})
        self.assertEqual([s["status"] for s in t.list()], ["waiting", "idle"])
        self.assertEqual(t.sessions[("claude", "b")]["cwd"], "/p/two")
        t.update({"agent": "claude", "session_id": "b", "event": "SessionEnd"})
        self.assertEqual(len(t.list()), 1)
        now[0] += agents.SESSION_TTL + 1
        t.prune()
        self.assertEqual(t.list(), [])
        self.assertEqual(agents.project_name("C:\\code\\mini-bro\\"), "mini-bro")


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addr = os.path.join(self.tmp.name, "s.sock")
        with open(os.path.join(self.tmp.name, "token"), "w") as f:
            f.write("secret")
        self.env = dict(os.environ, MINIBRO_ADDR=self.addr, MINIBRO_HOME=self.tmp.name)
        self.events = queue.Queue()

    def serve(self, **kw):
        srv = Server("secret", self.events, addr=self.addr, **kw)
        srv.start()
        self.addCleanup(srv.close)
        return srv

    def run_hook(self, payload, timeout=10):
        t0 = time.time()
        p = subprocess.run([sys.executable, "-I", "-S", HOOK, "--agent", "claude"], input=json.dumps(payload),
                           capture_output=True, text=True, env=self.env, timeout=timeout)
        return p.stdout, time.time() - t0

    def answer_with(self, decision):
        def ui():
            kind, card = self.events.get(timeout=5)
            self.assertEqual(kind, "card")
            self.assertEqual(card.tool, "Bash")
            card.shown.set()
            card.finish(decision)
        threading.Thread(target=ui, daemon=True).start()

    PERM = {"hook_event_name": "PermissionRequest", "session_id": "s1", "cwd": "/p",
            "tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": "/secret"}

    def test_allow(self):
        self.serve()
        self.answer_with("allow")
        out, _ = self.run_hook(self.PERM)
        self.assertEqual(json.loads(out), {"hookSpecificOutput": {
            "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}})

    def test_deny(self):
        self.serve()
        self.answer_with("deny")
        out, _ = self.run_hook(self.PERM)
        decision = json.loads(out)["hookSpecificOutput"]["decision"]
        self.assertEqual(decision["behavior"], "deny")
        self.assertIn("message", decision)

    def test_no_island_running_is_silent_and_fast(self):
        out, elapsed = self.run_hook(self.PERM)
        self.assertEqual(out, "")
        self.assertLess(elapsed, 3)

    def test_card_never_shown_declines_quickly(self):
        self.serve(shown_timeout=0.2)
        out, elapsed = self.run_hook(self.PERM)
        self.assertEqual(out, "")
        self.assertLess(elapsed, 3)

    def test_question_tool_is_left_to_terminal(self):
        self.serve()
        out, _ = self.run_hook(dict(self.PERM, tool_name="AskUserQuestion"))
        self.assertEqual(out, "")
        self.assertTrue(self.events.empty())

    def test_fire_and_forget_event_is_delivered_and_trimmed(self):
        self.serve()
        big = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/p", "prompt": "hi",
               "tool_response": "x" * 10, "transcript_path": "/secret"}
        out, _ = self.run_hook(big)
        self.assertEqual(out, "")
        kind, msg = self.events.get(timeout=5)
        self.assertEqual((kind, msg["event"], msg["agent"], msg["session_id"]), ("event", "UserPromptSubmit", "claude", "s1"))
        self.assertNotIn("transcript_path", json.dumps(msg))

    def test_long_strings_are_capped(self):
        self.serve()
        self.run_hook({"hook_event_name": "Notification", "session_id": "s", "message": "m" * 5000})
        _, msg = self.events.get(timeout=5)
        self.assertLessEqual(len(msg["message"]), 2049)

    def test_agent_flag_and_codex_permission(self):
        self.serve()
        self.answer_with("allow")
        p = subprocess.run([sys.executable, "-I", "-S", HOOK, "--agent", "codex"], input=json.dumps(self.PERM),
                           capture_output=True, text=True, env=self.env, timeout=10)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_gemini_notification_is_status_only(self):
        self.serve()
        p = subprocess.run([sys.executable, "-I", "-S", HOOK, "--agent", "gemini"], capture_output=True, text=True,
                           input=json.dumps({"hook_event_name": "Notification", "notification_type": "ToolPermission",
                                             "session_id": "g1", "cwd": "/p", "message": "needs permission"}),
                           env=self.env, timeout=10)
        self.assertEqual(p.stdout, "")
        kind, msg = self.events.get(timeout=5)
        self.assertEqual((kind, msg["agent"], msg["event"]), ("event", "gemini", "Notification"))

    def test_wrong_token_is_ignored(self):
        self.serve()
        with open(os.path.join(self.tmp.name, "token"), "w") as f:
            f.write("wrong")
        self.run_hook({"hook_event_name": "Stop", "session_id": "s"})
        time.sleep(0.3)
        self.assertTrue(self.events.empty())


class MultiAgentTests(unittest.TestCase):
    CMD = '"python" -I -S "C:/x/minibro-hook.py" --agent {}'

    def merged(self, agent, base=None, **kw):
        return agents.merge_hooks(base or {}, self.CMD.format(agent), agent, **kw)

    def test_codex_shape(self):
        out = self.merged("codex", command_windows='"python" "C:\\x\\minibro-hook.py" --agent codex')
        self.assertEqual(set(out["hooks"]), {"SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest", "Stop"})
        perm = out["hooks"]["PermissionRequest"][0]
        self.assertEqual(perm["matcher"], "*")
        self.assertEqual(perm["hooks"][0]["timeout"], 120)  # seconds
        self.assertIn("commandWindows", perm["hooks"][0])
        self.assertLessEqual(out["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)  # Codex caps SessionEnd at 3 s
        self.assertTrue(agents.is_connected(out))

    def test_codex_keeps_description_and_other_hooks(self):
        base = {"description": "mine", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}
        out = self.merged("codex", base)
        self.assertEqual(out["description"], "mine")
        self.assertEqual(out["hooks"]["PreToolUse"], base["hooks"]["PreToolUse"])
        self.assertEqual(agents.remove_hooks(out), base)

    def test_gemini_shape(self):
        out = self.merged("gemini", {"theme": "dark"})
        self.assertEqual(out["theme"], "dark")
        self.assertEqual(set(out["hooks"]), {"SessionStart", "SessionEnd", "BeforeAgent", "AfterAgent", "Notification"})
        self.assertNotIn("PermissionRequest", out["hooks"])  # Gemini cannot answer permission prompts
        handler = out["hooks"]["BeforeAgent"][0]["hooks"][0]
        self.assertEqual(handler["timeout"], 5000)  # milliseconds
        self.assertEqual(handler["name"], agents.MARKER)
        self.assertNotIn("matcher", out["hooks"]["BeforeAgent"][0])
        self.assertEqual(agents.remove_hooks(out), {"theme": "dark"})

    def test_settings_paths(self):
        self.assertTrue(agents.settings_path("codex").endswith(os.path.join(".codex", "hooks.json")))
        self.assertTrue(agents.settings_path("gemini").endswith(os.path.join(".gemini", "settings.json")))

    def test_native_command_keeps_backslashes(self):
        cmd = agents.hook_command(r"C:\Py\python.exe", r"C:\a\minibro-hook.py", "codex", native=True)
        self.assertIn("\\", cmd)
        self.assertIn("--agent codex", cmd)

    def test_tracker_gemini_events(self):
        t = agents.Tracker()
        t.update({"agent": "gemini", "session_id": "g", "event": "BeforeAgent", "cwd": "/p"})
        self.assertEqual(t.list()[0]["status"], "working")
        t.update({"agent": "gemini", "session_id": "g", "event": "Notification"})
        self.assertEqual(t.list()[0]["status"], "waiting")
        t.update({"agent": "gemini", "session_id": "g", "event": "AfterAgent"})
        self.assertEqual(t.list()[0]["status"], "idle")
        self.assertEqual(agents.agent_name("gemini"), "Gemini CLI")
        self.assertEqual(agents.agent_name("codex"), "Codex")


if __name__ == "__main__":
    unittest.main()
