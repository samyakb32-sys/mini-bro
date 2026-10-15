"""Top-of-screen island. A thin strip that slides out on hover, drop, or when an agent needs you.

One window, one canvas, everything drawn by hand: no per-row widgets. Tk stays on the main thread;
the pipe server and URL downloads run on short threads and hand results over through queues.
Animation timers only run during the ~130 ms open/close transitions.

Tabs: Shelf (temporary files) and Agents (live coding-agent sessions). Permission requests from
agents appear as Allow/Deny cards above the tabs and pop the island open.
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox

from tkinterdnd2 import COPY, DND_FILES, DND_TEXT, TkinterDnD

from . import agents, autostart, clipboard
from .fetch import download, is_url

TRANSPARENT = "#010203"
BLACK = "#000000"
TEXT = "#f2f2f7"
DIM = "#8e8e93"
ACCENT = "#0a84ff"
ORANGE = "#ff9f0a"
GREEN = "#30d158"
ROW_HOVER = "#1c1c1e"
BTN = "#2c2c2e"
BAR = "#3a3a3c"

W = 340  # expanded width
HEADER = 46
ROW = 46
CARD_H = 100
MAX_CARDS = 3
PAD = 14
RADIUS = 24
MAX_ROWS = 6
STRIP_W, STRIP_H = 132, 6  # collapsed hit area
FRAMES, FRAME_MS = 9, 14
POLL_MS = 200
FONT = "Segoe UI"
STATUS_COLOR = {"waiting": ORANGE, "working": GREEN, "idle": BAR}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def time_left(expires):
    secs = max(0, int(expires - time.time()))
    if secs >= 3600:
        return f"{secs // 3600}h {secs % 3600 // 60}m left"
    return f"{max(1, secs // 60)}m left"


def ago(ts):
    secs = max(0, int(time.time() - ts))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def ease(t):
    return 1 - (1 - t) ** 3


class Island:
    def __init__(self, store, config, events=None, tracker=None):
        self.store = store
        self.config = config
        self.events = events or queue.Queue()
        self.tracker = tracker or agents.Tracker()
        self.cards = []  # pending permission requests (server.Card)
        self.tab = "shelf"
        self.expanded = False
        self.drop_hover = False
        self.dragging = False
        self.menu_open = False
        self.hover_row = None
        self.busy = 0  # downloads in flight
        self.rows = []  # shelf rows: [(item_id, y0, y1)]
        self.buttons = []  # card buttons: [(card, decision, x0, y0, x1, y1)]
        self.w, self.h = STRIP_W, STRIP_H
        self._anim = None
        self._leave_job = None
        self._tick_job = None

        self.root = TkinterDnD.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass  # non-Windows dev: opaque corners, fine
        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.place(self.w, self.h)
        self.render()

        self.root.bind("<Enter>", lambda _e: self.expand())
        self.root.bind("<Leave>", lambda _e: self.schedule_leave_check())
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Button-1>", self.on_click)
        self.root.bind("<Control-v>", lambda _e: self.paste())

        self.canvas.drop_target_register(DND_FILES, DND_TEXT)
        self.canvas.dnd_bind("<<DropEnter>>", self.on_drop_enter)
        self.canvas.dnd_bind("<<DropLeave>>", self.on_drop_leave)
        self.canvas.dnd_bind("<<Drop>>", self.on_drop)
        self.canvas.drag_source_register(1, DND_FILES)
        self.canvas.dnd_bind("<<DragInitCmd>>", self.on_drag_init)
        self.canvas.dnd_bind("<<DragEndCmd>>", self.on_drag_end)

        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self.expiry_var = tk.IntVar(value=config["expiry_minutes"])
        self.menu = self.build_menu()
        self.purge_loop()
        self.poll_events()

    # --- geometry / animation ----------------------------------------------
    def place(self, w, h):
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{int(w)}x{int(h)}+{(sw - int(w)) // 2}+0")

    def cards_height(self):
        n = len(self.cards)
        return min(n, MAX_CARDS) * CARD_H + (20 if n > MAX_CARDS else 0)

    def body_count(self):
        return len(self.store.items) if self.tab == "shelf" else len(self.tracker.sessions)

    def target_height(self):
        count = self.body_count()
        body = max(min(count, MAX_ROWS), 1) * ROW + (22 if count > MAX_ROWS else 0)
        return HEADER + self.cards_height() + body + PAD

    def animate_to(self, w, h, done=None):
        if self._anim:
            self.root.after_cancel(self._anim)
        w0, h0, step = self.w, self.h, [0]

        def frame():
            step[0] += 1
            t = ease(step[0] / FRAMES)
            self.w, self.h = w0 + (w - w0) * t, h0 + (h - h0) * t
            self.place(self.w, self.h)
            self.render(content=False)
            if step[0] < FRAMES:
                self._anim = self.root.after(FRAME_MS, frame)
            else:
                self._anim = None
                self.w, self.h = w, h
                self.place(w, h)
                if done:
                    done()

        frame()

    def expand(self):
        self.cancel_leave()
        if self.expanded:
            return
        self.expanded = True
        self.store.purge_expired()
        self.animate_to(W, self.target_height(), self.render)
        self.schedule_tick()

    def collapse(self):
        if not self.expanded:
            return
        self.expanded = False
        self.hover_row = None
        if self._tick_job:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        self.render(content=False)
        self.animate_to(STRIP_W, STRIP_H, self.render)

    def resize_to_content(self):
        if self.expanded:
            self.animate_to(W, self.target_height(), self.render)
        else:
            self.render()

    # --- hover logic ---------------------------------------------------------
    def schedule_leave_check(self):
        self.cancel_leave()
        self._leave_job = self.root.after(350, self.leave_check)

    def cancel_leave(self):
        if self._leave_job:
            self.root.after_cancel(self._leave_job)
            self._leave_job = None

    def leave_check(self):
        self._leave_job = None
        if self.dragging or self.menu_open or self.drop_hover or self.cards:
            return
        px, py = self.root.winfo_pointerxy()
        x0, y0 = self.root.winfo_rootx(), self.root.winfo_rooty()
        if x0 <= px < x0 + self.w and y0 <= py < y0 + self.h:
            return
        self.collapse()

    def on_motion(self, event):
        row = None
        for item_id, y0, y1 in self.rows:
            if y0 <= event.y < y1:
                row = item_id
        if row != self.hover_row:
            self.hover_row = row
            self.render()

    # --- drawing -------------------------------------------------------------
    def rounded_bottom(self, w, h, r, **kw):
        """Island shape: flush with the top edge, rounded bottom corners."""
        r = min(r, h / 2, w / 2)
        pts = [0, 0, w, 0, w, h - r, w, h, w - r, h, r, h, 0, h, 0, h - r]
        return self.canvas.create_polygon(pts, smooth=True, splinesteps=12, **kw)

    def render(self, content=True):
        c = self.canvas
        c.delete("all")
        w, h = int(self.w), int(self.h)
        outline = ACCENT if self.drop_hover else BLACK
        self.rounded_bottom(w, h, RADIUS if h > 20 else h, fill=BLACK, outline=outline,
                            width=2 if self.drop_hover else 1)
        self.rows, self.buttons = [], []
        if not self.expanded:
            if h <= STRIP_H + 1:
                color = ORANGE if self.cards else ACCENT if self.store.items else BAR
                c.create_rectangle(w // 2 - 22, 1, w // 2 + 22, 4, fill=color, outline=color)
            return
        if not content or self.w < W - 1:
            return
        self.draw_header()
        y = HEADER + self.draw_cards()
        if self.tab == "shelf":
            self.draw_shelf(y)
        else:
            self.draw_agents(y)
        for card in self.cards[:MAX_CARDS]:
            card.shown.set()  # the card is on screen: the server may now wait for the answer

    def draw_header(self):
        c = self.canvas
        mid = HEADER // 2 + 2
        n_items, n_agents = len(self.store.items), len(self.tracker.sessions)
        for label, key, x, count in (("Shelf", "shelf", PAD + 4, n_items), ("Agents", "agents", PAD + 66, n_agents)):
            active = self.tab == key
            t = c.create_text(x, mid, text=label, anchor="w", fill=TEXT if active else DIM,
                              font=(FONT, 12 if active else 11, "bold" if active else "normal"))
            if count:
                bx = c.bbox(t)[2] + 4
                c.create_text(bx, mid + 1, text=str(count), anchor="w", fill=DIM, font=(FONT, 9))
        if self.busy:
            c.create_text(PAD + 160, mid + 1, text="Saving…", anchor="w", fill=ACCENT, font=(FONT, 9))
        if self.tab == "shelf":
            c.create_text(W - PAD - 38, mid, text="Paste", anchor="e", fill=ACCENT, font=(FONT, 10))
        c.create_text(W - PAD - 6, HEADER // 2 + 1, text="⋯", anchor="e", fill=DIM, font=(FONT, 16, "bold"))

    def draw_cards(self):
        c = self.canvas
        y = HEADER
        for card in self.cards[:MAX_CARDS]:
            x0, x1, y0, y1 = PAD - 4, W - PAD + 4, y + 4, y + CARD_H - 4
            c.create_rectangle(x0, y0, x1, y1, fill=ROW_HOVER, outline=ORANGE)
            c.create_text(x0 + 10, y0 + 14, anchor="w", fill=ORANGE, font=(FONT, 9, "bold"),
                          text=f"{agents.agent_name(card.agent)} · {agents.project_name(card.cwd)}")
            c.create_text(x0 + 10, y0 + 28, anchor="nw", fill=TEXT, font=(FONT, 9), width=x1 - x0 - 20,
                          text=agents.summarize_tool(card.tool, card.tool_input, 110))
            by0, by1 = y1 - 32, y1 - 8
            for decision, label, bx1, fill in (("allow", "Allow", x1 - 10, ACCENT), ("deny", "Deny", x1 - 94, BTN)):
                bx0 = bx1 - 78
                c.create_rectangle(bx0, by0, bx1, by1, fill=fill, outline=fill)
                c.create_text((bx0 + bx1) // 2, (by0 + by1) // 2, text=label, fill="white", font=(FONT, 10, "bold"))
                self.buttons.append((card, decision, bx0, by0, bx1, by1))
            y += CARD_H
        extra = len(self.cards) - MAX_CARDS
        if extra > 0:
            c.create_text(W // 2, y + 10, text=f"+{extra} more waiting", fill=ORANGE, font=(FONT, 9))
        return self.cards_height()

    def draw_shelf(self, y):
        c = self.canvas
        items = self.store.list()
        if not items:
            c.create_text(W // 2, y + ROW // 2, fill=DIM, font=(FONT, 10),
                          text="Drop files or images here, or press Ctrl+V to paste.")
            return
        for item in items[:MAX_ROWS]:
            y0, y1 = y, y + ROW
            self.rows.append((item["id"], y0, y1))
            if item["id"] == self.hover_row:
                c.create_rectangle(PAD - 4, y0 + 2, W - PAD + 4, y1 - 2, fill=ROW_HOVER, outline=ROW_HOVER)
            name = item["name"] if len(item["name"]) <= 30 else item["name"][:27] + "..."
            c.create_text(PAD + 6, y0 + 15, text=name, anchor="w", fill=TEXT, font=(FONT, 10))
            c.create_text(PAD + 6, y0 + 32, anchor="w", fill=DIM, font=(FONT, 8),
                          text=f"{human_size(item['size'])}  ·  {time_left(item['expires'])}")
            if item["id"] == self.hover_row:
                c.create_text(W - PAD - 8, (y0 + y1) // 2, text="✕", fill=DIM, font=(FONT, 11))
            y = y1
        extra = len(items) - MAX_ROWS
        if extra > 0:
            c.create_text(W // 2, y + 11, text=f"+{extra} more", fill=DIM, font=(FONT, 9))

    def draw_agents(self, y):
        c = self.canvas
        sessions = self.tracker.list()
        if not sessions:
            c.create_text(W // 2, y + ROW // 2, fill=DIM, font=(FONT, 10),
                          text="No agents yet. Use ⋯ then Connect Claude Code.")
            return
        for s in sessions[:MAX_ROWS]:
            y0, y1 = y, y + ROW
            color = STATUS_COLOR[s["status"]]
            c.create_oval(PAD + 4, y0 + 14, PAD + 12, y0 + 22, fill=color, outline=color)
            c.create_text(PAD + 22, y0 + 17, text=agents.project_name(s["cwd"]), anchor="w", fill=TEXT, font=(FONT, 10))
            c.create_text(PAD + 22, y0 + 34, anchor="w", fill=DIM, font=(FONT, 8),
                          text=f"{agents.agent_name(s['agent'])}  ·  {s['status']}  ·  {ago(s.get('updated', time.time()))}")
            y = y1
        extra = len(sessions) - MAX_ROWS
        if extra > 0:
            c.create_text(W // 2, y + 11, text=f"+{extra} more", fill=DIM, font=(FONT, 9))

    # --- clicks / menu ---------------------------------------------------------
    def set_tab(self, tab):
        if tab != self.tab:
            self.tab = tab
            self.hover_row = None
            self.resize_to_content()

    def on_click(self, event):
        if not self.expanded:
            return
        self.root.focus_force()  # so Ctrl+V works after clicking the island
        for card, decision, x0, y0, x1, y1 in self.buttons:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.answer(card, decision)
                return
        if event.y < HEADER:
            if event.x > W - PAD - 40:
                self.open_menu()
            elif self.tab == "shelf" and W - PAD - 100 < event.x <= W - PAD - 40:
                self.paste()
            elif event.x < PAD + 62:
                self.set_tab("shelf")
            elif event.x < PAD + 150:
                self.set_tab("agents")
            return
        if self.tab == "shelf" and self.hover_row and event.x > W - PAD - 30:
            self.store.remove(self.hover_row)
            self.hover_row = None
            self.resize_to_content()

    def open_menu(self):
        self.menu_open = True
        try:
            self.menu.tk_popup(self.root.winfo_rootx() + W - 150, self.root.winfo_rooty() + HEADER)
        finally:
            self.menu.grab_release()
            self.menu_open = False
            self.schedule_leave_check()

    def build_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        expiry = tk.Menu(menu, tearoff=0)
        for minutes, label in ((5, "5 minutes"), (15, "15 minutes"), (60, "1 hour"),
                               (240, "4 hours"), (1440, "24 hours")):
            expiry.add_radiobutton(label=label, value=minutes, variable=self.expiry_var,
                                   command=lambda m=minutes: self.config.set(expiry_minutes=m))
        menu.add_cascade(label="Keep files for", menu=expiry)
        menu.add_checkbutton(label="Start with Windows", variable=self.autostart_var,
                             command=lambda: autostart.set_enabled(self.autostart_var.get()))
        menu.add_separator()
        menu.add_command(label="Connect Claude Code…", command=lambda: self.connect_claude(False))
        menu.add_command(label="Disconnect Claude Code…", command=lambda: self.connect_claude(True))
        menu.add_separator()
        menu.add_command(label="Clear shelf", command=self.clear_all)
        menu.add_command(label="Quit", command=self.root.destroy)
        return menu

    def clear_all(self):
        self.store.clear()
        self.resize_to_content()

    # --- agents: cards, events, hook install -------------------------------------
    def answer(self, card, decision):
        card.finish(decision)
        if card in self.cards:
            self.cards.remove(card)
        self.tracker.set_status(card.agent, card.session_id, "working" if decision == "allow" else "idle")
        self.resize_to_content()
        if not self.cards:
            self.schedule_leave_check()

    def poll_events(self):
        new_card = changed = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                changed = True
                if kind == "card":
                    self.cards.append(payload)
                    new_card = True
                    self.tracker.update({"agent": payload.agent, "session_id": payload.session_id,
                                         "event": "PermissionRequest", "cwd": payload.cwd})
                else:
                    self.tracker.update(payload)
        except queue.Empty:
            pass
        live = [c for c in self.cards if not c.done.is_set()]  # the server declines cards after its timeout
        if len(live) != len(self.cards):
            self.cards, changed = live, True
        if new_card and not self.expanded:
            self.expand()
        elif changed:
            self.resize_to_content()
        self.root.after(POLL_MS, self.poll_events)

    def connect_claude(self, disconnect):
        path = agents.claude_settings_path()
        self.menu_open = True
        try:
            try:
                data, text = agents.read_settings(path)
            except ValueError as e:
                messagebox.showerror("Mini-Bro", f"{path} is not valid JSON ({e}). Nothing was changed.")
                return
            if disconnect:
                new = agents.remove_hooks(data)
            else:
                python_exe, hook_path = agents.hook_target()
                new = agents.merge_hooks(data, agents.hook_command(python_exe, hook_path))
            patch = agents.diff(text, new)
            if not patch:
                messagebox.showinfo("Mini-Bro", "Already up to date. Nothing to change.")
                return
            title = "Disconnect Claude Code" if disconnect else "Connect Claude Code"
            if self.confirm_diff(title, path, patch):
                backup = agents.apply_settings(path, new)
                messagebox.showinfo("Mini-Bro", "Saved." + (f"\nBackup: {backup}" if backup else "")
                                    + ("\nRestart Claude Code sessions to pick up the change." if not disconnect else ""))
        finally:
            self.menu_open = False
            self.schedule_leave_check()

    def confirm_diff(self, title, path, patch):
        top = tk.Toplevel(self.root)
        top.title(title)
        top.attributes("-topmost", True)
        top.geometry("640x420")
        tk.Label(top, anchor="w", justify="left", text=f"These changes will be written to:\n{path}\n"
                 "A dated backup is made first. Nothing is written until you click Apply.").pack(fill="x", padx=10, pady=8)
        box = tk.Text(top, wrap="none", font=("Consolas", 9))
        box.insert("1.0", patch)
        for i, line in enumerate(patch.splitlines(), 1):
            if line.startswith("+") and not line.startswith("+++"):
                box.tag_add("add", f"{i}.0", f"{i}.end")
            elif line.startswith("-") and not line.startswith("---"):
                box.tag_add("del", f"{i}.0", f"{i}.end")
        box.tag_config("add", foreground="#1a7f37")
        box.tag_config("del", foreground="#cf222e")
        box.config(state="disabled")
        box.pack(fill="both", expand=True, padx=10)
        result = []
        bar = tk.Frame(top)
        bar.pack(fill="x", padx=10, pady=8)
        tk.Button(bar, text="Apply", width=10, command=lambda: (result.append(True), top.destroy())).pack(side="right")
        tk.Button(bar, text="Cancel", width=10, command=top.destroy).pack(side="right", padx=6)
        top.grab_set()
        self.root.wait_window(top)
        return bool(result)

    # --- shelf: drag and drop --------------------------------------------------------
    def on_drop_enter(self, event):
        self.drop_hover = True
        self.tab = "shelf"
        self.expand()
        self.render()
        return event.action

    def on_drop_leave(self, event):
        self.drop_hover = False
        self.render()
        self.schedule_leave_check()
        return event.action

    def on_drop(self, event):
        self.drop_hover = False
        for part in self.root.tk.splitlist(event.data):
            if is_url(part):
                self.fetch_url(part)
            else:
                self.add_path(part)
        self.resize_to_content()
        return COPY

    def ttl(self):
        return self.config["expiry_minutes"] * 60

    def add_path(self, path):
        try:
            self.store.add(path, self.ttl())
        except OSError:
            pass

    def fetch_url(self, url):
        """Download an image dragged from a browser on a short-lived thread; Tk stays on the main thread."""
        self.busy += 1
        result = []

        def work():
            try:
                result.append(download(url))
            except Exception:
                result.append(None)

        threading.Thread(target=work, daemon=True).start()
        self.poll_fetch(result)

    def poll_fetch(self, result):
        if not result:
            self.render(content=self.expanded)
            self.root.after(250, self.poll_fetch, result)
            return
        self.busy -= 1
        if result[0]:
            self.store.add_bytes(result[0][0], result[0][1], self.ttl())
        self.resize_to_content()

    def paste(self):
        if self.tab != "shelf":
            return
        got = clipboard.read()
        if not got:
            return
        kind, payload = got
        if kind == "files":
            for path in payload:
                self.add_path(path)
        else:
            self.store.add_bytes(time.strftime("pasted-%H%M%S.png"), payload, self.ttl())
        self.resize_to_content()

    def on_drag_init(self, event):
        for item_id, y0, y1 in self.rows:
            if y0 <= event.y < y1 and event.x < W - PAD - 30:
                self.dragging = True
                path = self.store.items[item_id]["path"]
                return (COPY, DND_FILES, self.root.tk.merge(path))
        return None  # not on a row: no drag

    def on_drag_end(self, _event):
        self.dragging = False
        self.schedule_leave_check()

    # --- timers ----------------------------------------------------------------------
    def schedule_tick(self):
        # Refresh "time left" / "ago" labels only while the panel is open.
        self._tick_job = self.root.after(30000, self.tick)

    def tick(self):
        self.store.purge_expired()
        self.tracker.prune()
        self.resize_to_content()
        if self.expanded:
            self.schedule_tick()

    def purge_loop(self):
        if self.store.purge_expired():
            self.resize_to_content()
        self.root.after(60000, self.purge_loop)

    def run(self):
        self.root.mainloop()
