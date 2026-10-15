# Mini-Bro

A tiny Windows "island" docked at the top of the screen that holds files for a
short time. It rests as a thin strip; hover it (or drag a file toward it) and it
slides out into a black shelf. Drag items back out to anywhere. Held items are
deleted automatically when they expire.

Built to be light: stdlib `tkinter` plus `tkinterdnd2` for drag and drop. It sits
idle (one 60-second timer; a short-lived thread runs only while a browser image downloads) so it runs fine on old PCs.

## Run

```
pip install -r requirements.txt
python -m minibro.main
```

## Use

- **Hover** the strip at the top-center of the screen to open the shelf; move away to close it.
- **Drop** files or folders onto the strip or the open shelf.
- **Paste** with Ctrl+V (click the island first) or the Paste button: copied files, or a copied image or screenshot.
- **Drag images from a browser** onto the island; they are downloaded (up to 50 MB) and held like any file.
- **Drag** a row out to copy the item elsewhere. Hover a row and click ✕ to remove it.
- **⋯** menu: how long to keep files (default 1 hour), Start with Windows, Clear all, Quit.

Small items (under 200 MB) are copied into `%LOCALAPPDATA%\MiniBro\shelf`, so the
original can move or be deleted. Larger ones are referenced in place; expiry only
forgets them and never deletes your original.

## Coding agents (Claude Code, Codex, Gemini CLI)

Mini-Bro lists every live agent session in one **Agents** tab, and shows permission prompts as cards on the island.

| Agent | Hook file Mini-Bro edits | Allow / Deny cards | Status in Agents tab |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` | yes | yes |
| Codex | `~/.codex/hooks.json` | yes | yes |
| Gemini CLI | `~/.gemini/settings.json` | no (see below) | yes |

1. Hover the island, open **⋯ → Connect agent → (pick one)**.
2. You get a diff of the changes. Nothing is written until you click **Apply**; a dated backup is made first. If the file isn't valid JSON, nothing is touched.
3. Restart the agent (in Codex, also run `/hooks` once to review and trust the new hooks; Codex skips untrusted hooks).
4. From then on:
   - A permission request pops the island open with an **Allow / Deny** card (agent, project, command). The answer goes straight back to the agent.
   - The **Agents** tab lists each session with a status dot: working (green), waiting for you (orange), idle (grey). The orange dot clears on the agent's next event, not the moment you approve in the terminal.
   - **⋯ → Disconnect agent** removes only the entries Mini-Bro added.

**Gemini CLI** has no hook that can answer a permission prompt: its `Notification` event is observe-only. So Gemini sessions show up with status, but approving stays in the terminal.

It never blocks the agent. If Mini-Bro isn't running, or you don't answer within about 108 seconds, nothing is returned and the agent asks in its terminal as usual. `AskUserQuestion` prompts are always left to the terminal. Codex covers shell, `apply_patch` and MCP tools only (its permission hook doesn't see hosted tools like web search).

How it works: each agent runs a tiny relay (`minibro-hook.py` in `%LOCALAPPDATA%\MiniBro`, or `minibro-hook.exe` in a build) for a few events (session start/end, prompt, permission request, stop; Gemini uses `BeforeAgent`/`AfterAgent`/`Notification`). The relay trims the payload and sends it over a per-user named pipe, authenticated with a random token stored in the app folder. The relay gives up after 0.3 s if the app isn't there. The app declines at once if the card isn't on screen within 0.8 s.

Adding another agent means adding an entry to `SPECS` in `minibro/agents.py` (hook file and events) and, if its field names differ, mapping them in `normalize()` in `minibro/hook.py`.

## Build an exe

```
build.bat
```

Produces `dist\MiniBro.exe` and `dist\minibro-hook.exe`; keep them in the same folder.

## Tests

```
python -m unittest discover -s tests -t .
```

Covers the storage and expiry logic. The GUI, drag and drop, and autostart are
Windows-only and need a manual check.
