"""On-screen HUD: an arc-reactor ring plus what JARVIS heard and said.

    idle          dim ring, turning slowly       (tucks away to just the ring after a while)
    listening     bright cyan, pulsing           (after "Hey Jarvis", or when you talk over it)
    follow_up     softer cyan                    ("anything else?" window, no wake word needed)
    transcribing  pale blue, quick               (Whisper is working)
    thinking      amber, spinning fast           (the LLM is working)
    speaking      cyan, reacting to its voice    (taken from the Speaker automatically)

It's a borderless, always-on-top window in a corner of the screen that your
mouse clicks go straight through, so it never gets in the way. It doesn't show
in Alt+Tab or the taskbar.

tkinter (built into Python) runs on its own thread; everything else talks to it
through a queue, so state()/heard()/reply() are safe to call from any thread.
"""

from __future__ import annotations

import math
import gc
import queue
import sys
import threading
import time

# (label, colour, spin speed, glow) per state.
STATES: dict[str, tuple[str, str, float, float]] = {
    "idle":         ("STANDING BY",  "#2a8fa3", 0.5, 0.25),
    "listening":    ("LISTENING",    "#4ff3ff", 2.0, 1.00),
    "follow_up":    ("GO AHEAD",     "#4ff3ff", 1.2, 0.65),
    "transcribing": ("TRANSCRIBING", "#8fd9ff", 3.5, 0.75),
    "thinking":     ("THINKING",     "#ffb347", 6.0, 0.90),
    "speaking":     ("SPEAKING",     "#4ff3ff", 1.5, 0.60),
    "paused":       ("PAUSED",       "#5b6b70", 0.15, 0.10),
}

_KEY = "#010203"        # drawn in this colour = see-through (Windows colour key)
_PANEL = "#08161b"
_TEXT_DIM = "#9fb4ba"
_TEXT = "#e6fbff"
_FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
_MONO = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"


def mix(a: str, b: str, t: float) -> str:
    """Blend two #rrggbb colours (t=0 -> a, t=1 -> b). tkinter has no alpha, so glows are blends."""
    t = max(0.0, min(1.0, t))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


class HudView:
    """Draws one frame onto a tkinter-style canvas. Kept separate from the window so it can be previewed."""

    def __init__(self, scale: float = 1.0, align: str = "right"):
        self.s = scale
        self.align = align                      # which side of the screen: the ring sits on that edge
        self.width = int(400 * scale)
        self.height = int(150 * scale)

    def draw(self, c, state: str, t: float, level: float, heard: str, reply: str,
             compact: bool, detail: str = "") -> None:
        s = self.s
        label, colour, speed, glow = STATES.get(state, STATES["idle"])
        if state == "speaking":
            glow = 0.35 + 0.65 * level
        c.delete("all")

        R = 34 * s                               # ring radius
        pad = 8 * s
        cx = self.width - pad - R if self.align == "right" else pad + R
        cy = self.height - pad - R               # ring sits in the bottom corner

        # --- Text panel ------------------------------------------------------
        if not compact:
            panel_w = self.width - 2 * R - 3 * pad
            x0 = pad if self.align == "right" else 2 * R + 2 * pad
            x1 = x0 + panel_w
            text_x = x0 + 12 * s
            wrap = panel_w - 24 * s
            items = [(label + (f"  ·  {detail}" if detail else ""), (_MONO, int(8 * s), "bold"), colour)]
            if heard:
                items.append((f"“{_shorten(heard, 110)}”", (_FONT, int(9 * s), "italic"), _TEXT_DIM))
            if reply:
                items.append((_shorten(reply, 220), (_FONT, int(10 * s)), _TEXT))

            # Lay the text out bottom-up from the ring's base, so the panel grows upwards.
            ids, y = [], 0.0
            for text, font, fill in items:
                ids.append(c.create_text(text_x, y, text=text, font=font, fill=fill,
                                         anchor="nw", width=wrap))
                bbox = c.bbox(ids[-1])
                y = bbox[3] + 5 * s
            content_h = y - 5 * s
            panel_h = content_h + 20 * s
            if panel_h <= 2 * R:                 # short: centred on the ring
                top, bottom = cy - panel_h / 2, cy + panel_h / 2
            else:                                # tall: grows upwards from the ring's base
                bottom = cy + R
                top = max(pad, bottom - panel_h)
            shift = top + 10 * s
            for i in ids:
                c.move(i, 0, shift)
            # Panel behind the text (rounded), with a thin line in the state colour.
            rounded_rect(c, x0, top, x1, bottom, 10 * s, fill=_PANEL,
                         outline=mix(colour, _PANEL, 0.55), width=max(1, int(s)))
            for i in ids:
                c.tag_raise(i)
            # Little connector from the panel to the ring.
            near = x1 if self.align == "right" else x0
            c.create_line(near, cy, cx - R if self.align == "right" else cx + R, cy,
                          fill=mix(colour, _PANEL, 0.5), width=max(1, int(s)))

        # --- Arc-reactor ring ------------------------------------------------
        bg = _PANEL
        c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=bg, outline=mix(colour, bg, 0.6), width=max(1, int(s)))
        angle = (t * 60 * speed) % 360
        # Three rotating arcs.
        r2 = R * 0.78
        for k in range(3):
            c.create_arc(cx - r2, cy - r2, cx + r2, cy + r2, start=angle + k * 120, extent=70,
                         style="arc", outline=colour, width=max(2, int(3 * s)))
        # Counter-rotating tick ring.
        r3 = R * 0.58
        for k in range(12):
            c.create_arc(cx - r3, cy - r3, cx + r3, cy + r3, start=-angle * 0.6 + k * 30, extent=9,
                         style="arc", outline=mix(colour, bg, 0.45), width=max(1, int(2 * s)))
        # Glowing core: concentric fills fading into the background.
        pulse = 0.5 + 0.5 * math.sin(t * (3.0 if state in ("listening", "follow_up") else 1.6))
        core = R * (0.26 + 0.08 * glow * pulse)
        for i, f in enumerate((1.9, 1.5, 1.2, 1.0)):
            r = core * f
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="",
                          fill=mix(bg, colour, glow * (0.18 + 0.27 * i)))
        r = core * 0.45
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="", fill=mix(colour, "#ffffff", 0.6 * glow))


def rounded_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


class Hud:
    """The always-on-top window. Call start(); then state()/heard()/reply() from anywhere."""

    def __init__(self, position: str = "bottom-right", scale: float = 1.0, compact_after_s: float = 8.0):
        self.position = position
        self.scale = scale
        self.compact_after_s = compact_after_s
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._speaker = None
        self._thread: threading.Thread | None = None

    # --- Public API (any thread) ------------------------------------------

    def start(self) -> "Hud":
        self._thread = threading.Thread(target=self._run, daemon=True, name="hud")
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            raise self._error
        return self

    def attach_speaker(self, speaker) -> None:
        self._speaker = speaker  # read from the HUD thread to animate "speaking"

    def state(self, name: str, detail: str = "") -> None:
        self._queue.put(("state", (name, detail)))

    def heard(self, text: str) -> None:
        self._queue.put(("heard", text))

    def reply(self, text: str) -> None:
        self._queue.put(("reply", text))

    def close(self) -> None:
        self._queue.put(("close", None))
        # Wait for the HUD thread to tear Tk down itself. If Python exits first,
        # Tk is cleaned up from the main thread ("Tcl_AsyncDelete: ... wrong thread").
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    # --- HUD thread ----------------------------------------------------------

    def _run(self) -> None:
        try:
            import tkinter as tk
            _dpi_aware()
            root = tk.Tk()
            root.withdraw()
            # Windows reports DPI here once the process is DPI-aware; scale everything with it.
            scale = self.scale * max(1.0, root.winfo_fpixels("1i") / 96)
            align = "left" if "left" in self.position else "right"
            view = HudView(scale, align)
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg=_KEY)
            try:
                root.attributes("-transparentcolor", _KEY)
            except tk.TclError:
                pass  # not Windows: the corners just show the key colour
            canvas = tk.Canvas(root, width=view.width, height=view.height, bg=_KEY, highlightthickness=0)
            canvas.pack()
            root.geometry(f"{view.width}x{view.height}+{_corner(root, view, self.position)}")
            root.deiconify()
            root.update_idletasks()
            _click_through(root)
        except Exception as e:  # no display, no Tk...
            self._error = e
            self._ready.set()
            return
        self._ready.set()

        st = {"state": "idle", "detail": "", "heard": "", "reply": "", "active": time.monotonic()}
        start = time.monotonic()
        level = 0.0

        def frame():
            nonlocal level
            while True:
                try:
                    kind, value = self._queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "close":
                    root.destroy()
                    return
                if kind == "state":
                    st["state"], st["detail"] = value
                    if value[0] == "listening":
                        st["heard"], st["reply"] = "", ""   # a new command starts with a clean panel
                elif kind == "heard":
                    st["heard"], st["reply"] = value, ""    # follow-ups keep the last reply until now
                elif kind == "reply":
                    st["reply"] = value
                st["active"] = time.monotonic()

            speaking = bool(self._speaker and self._speaker.speaking)
            state = "speaking" if speaking and st["state"] in ("idle", "thinking") else st["state"]
            if speaking:
                st["active"] = time.monotonic()
                target = min(1.0, self._speaker.playback_rms() / 5000)
                level += (target - level) * 0.35
            else:
                level *= 0.8
            compact = state == "idle" and time.monotonic() - st["active"] > self.compact_after_s
            view.draw(canvas, state, time.monotonic() - start, level, st["heard"], st["reply"],
                      compact, st["detail"])
            root.after(33, frame)

        frame()
        try:
            root.mainloop()
        finally:
            # `frame` re-schedules itself, so it refers to itself: a reference cycle that
            # keeps root and canvas alive. Left to the garbage collector, they'd be freed
            # later on the main thread, which Tk reports as "Tcl_AsyncDelete: async
            # handler deleted by the wrong thread". Break the cycle and free them here.
            frame = canvas = view = root = None
            gc.collect()


# --- Windows helpers --------------------------------------------------------

def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _corner(root, view: HudView, position: str) -> str:
    """'+x+y' for the chosen corner of the work area (the screen minus the taskbar)."""
    left, top = 0, 0
    right, bottom = root.winfo_screenwidth(), root.winfo_screenheight()
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    margin = int(16 * view.s)
    x = left + margin if "left" in position else right - view.width - margin
    y = top + margin if "top" in position else bottom - view.height - margin
    return f"{x}+{y}"


def _click_through(root) -> None:
    """Let mouse clicks pass through the HUD, and keep it out of Alt+Tab."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000  # LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


class NullHud:
    """Stands in when the HUD is turned off or can't start, so callers never need to check."""

    def start(self):
        return self

    def attach_speaker(self, speaker):
        pass

    def state(self, name, detail=""):
        pass

    def heard(self, text):
        pass

    def reply(self, text):
        pass

    def close(self):
        pass
