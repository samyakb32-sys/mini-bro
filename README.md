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

## Coding agents (Claude Code)

Mini-Bro can show your coding agents' permission prompts on the island and list every live agent session.

1. Run the app, hover the island, open **⋯ → Connect Claude Code…**.
2. You get a diff of the changes to `~/.claude/settings.json`. Nothing is written until you click **Apply**, and a dated backup is made first. If the file isn't valid JSON, nothing is touched.
3. Restart your Claude Code sessions. From then on:
   - A permission request pops the island open with an **Allow / Deny** card (agent, project, command). The answer goes straight back to the agent.
   - The **Agents** tab lists each session with a status dot: working (green), waiting for you (orange), idle (grey).
   - **⋯ → Disconnect Claude Code…** removes only the entries Mini-Bro added.

It never blocks the agent. If Mini-Bro isn't running, or you don't answer within about 108 seconds, nothing is returned and Claude Code asks in its terminal as usual. `AskUserQuestion` prompts are always left to the terminal.

How it works: Claude Code runs a tiny relay (`minibro-hook.py` in `%LOCALAPPDATA%\MiniBro`, or `minibro-hook.exe` in a build) for six events (session start/end, prompt submit, permission request, notification, stop). The relay trims the payload and sends it over a per-user named pipe, authenticated with a random token stored in the app folder. The relay gives up after 0.3 s if the app isn't there. The app declines at once if the card isn't on screen within 0.8 s.

Only Claude Code is wired up so far. Other agents can be added by writing their hook config and field mapping (see `minibro/agents.py` and `normalize()` in `minibro/hook.py`).

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
