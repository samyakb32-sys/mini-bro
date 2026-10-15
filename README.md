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

## Build an exe

```
build.bat
```

Produces `dist\MiniBro.exe`.

## Tests

```
python -m unittest discover -s tests -t .
```

Covers the storage and expiry logic. The GUI, drag and drop, and autostart are
Windows-only and need a manual check.
