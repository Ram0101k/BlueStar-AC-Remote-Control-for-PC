#!/usr/bin/env python3
"""
Blue Star AC - native desktop app (tkinter, no browser, no web page).

Needs bluestar_pc.py in the same folder (it holds the login / cloud code).
Run:    python bluestar_app.py
Build:  build_exe.bat   ->  dist\\BlueStarAC.exe
"""
import math
import os
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox

import bluestar_pc as core

# ----------------------------------------------------------------------------- theme
FONT = "Segoe UI"
BG = "#0b111e"
CARD = "#151f32"
CARD_HOVER = "#1e2c45"
BORDER = "#253550"
BORDER_LIGHT = "#334668"
FG = "#f0f4fc"
MUT = "#8494b0"
DIM = "#3f4d66"
TRACK = "#1a273e"
ACC = "#3b82f6"
ACC_GLOW = "#1d4ed8"
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"

MODE_COLORS = [("cool", "#38bdf8"), ("heat", "#fb923c"), ("dry", "#2dd4bf"),
               ("auto", "#a78bfa"), ("ai", "#a78bfa"), ("fan", "#94a3b8")]

S = 1.0  # Dynamic scaling factor based on window dimensions
BASE_W = 440
BASE_H = 860


def px(n):
    return int(round(n * S))


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def sort_keys(d):
    keys = list(d)
    if all(str(k).lstrip("-").replace(".", "", 1).isdigit() for k in keys):
        return sorted(keys, key=float)
    return keys


def key_of(v):
    if v is None:
        return None
    return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)


def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


# ----------------------------------------------------------------------------- widgets
class Pill(tk.Canvas):
    """Modern rounded button with dynamic hover, focus, and scale."""

    def __init__(self, parent, text, command, height=38, size=10, bold=False, selected=False, color=ACC, width=90):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self.base_height = height
        self.base_width = width
        self.base_size = size
        self.bold = bold
        self.text, self.command, self.selected, self.color, self.hover = text, command, selected, color, False

        self.configure(width=px(self.base_width), height=px(self.base_height))
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Button-1>", lambda e: self.command())

    def _hover(self, v):
        self.hover = v
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), px(20)), max(self.winfo_height(), px(20))
        bg_parent = getattr(self.master, "bg_color", BG)
        self.configure(bg=bg_parent)

        if self.selected:
            fill, outline, fg = self.color, self.color, "#ffffff"
        else:
            fill = CARD_HOVER if self.hover else CARD
            outline = BORDER_LIGHT if self.hover else BORDER
            fg = FG if self.hover else MUT

        round_rect(self, 1, 1, w - 1, h - 1, h // 2, fill=fill, outline=outline, width=1)
        font = (FONT, max(6, px(self.base_size)), "bold" if (self.bold or self.selected) else "normal")
        self.create_text(w // 2, h // 2, text=self.text, fill=fg, font=font)

    def set(self, selected=None, text=None, color=None):
        changed = False
        for attr, val in (("selected", selected), ("text", text), ("color", color)):
            if val is not None and getattr(self, attr) != val:
                setattr(self, attr, val)
                changed = True
        if changed:
            self.draw()


class SectionCard(tk.Frame):
    """Container frame with background styling for grouped controls."""

    def __init__(self, parent, title, per_row=3, **kw):
        self.bg_color = CARD
        super().__init__(parent, bg=self.bg_color, highlightbackground=BORDER, highlightthickness=1, **kw)
        self.title = title
        self.per_row = per_row
        
        self.lbl_title = tk.Label(self, text=title.upper(), bg=CARD, fg=MUT, font=(FONT, max(6, px(8)), "bold"))
        self.lbl_title.grid(row=0, column=0, columnspan=per_row, sticky="w", padx=px(12), pady=(px(10), px(4)))

    def rescale(self):
        self.lbl_title.config(font=(FONT, max(6, px(8)), "bold"))


class PillGroup(SectionCard):
    def __init__(self, parent, title, options, on_pick, per_row=3, color=ACC):
        super().__init__(parent, title, per_row=per_row)
        self.options = options
        self.on_pick = on_pick
        self.color = color
        self.pills = {}
        self.build()

    def build(self):
        for c in range(self.per_row):
            self.columnconfigure(c, weight=1, uniform="p")

        for i, k in enumerate(sort_keys(self.options)):
            p = Pill(self, self.options[k], lambda k=k: self.on_pick(k), color=self.color, height=36, size=9)
            r = 1 + i // self.per_row
            col = i % self.per_row
            p.grid(row=r, column=col, sticky="ew", padx=px(6), pady=(px(2), px(8)))
            self.pills[str(k)] = p

    def rescale(self):
        super().rescale()
        for p in self.pills.values():
            p.config(height=px(36))
            p.draw()

    def show(self, current, color=None):
        for k, p in self.pills.items():
            p.set(selected=(k == current), color=color)

    def show_many(self, on_keys):
        for k, p in self.pills.items():
            p.set(selected=(k in on_keys))


class Dial(tk.Canvas):
    """Interactive arc dial supporting drag-to-set temperature, click-drag, and wheel."""
    BASE_W, BASE_H = 320, 290

    def __init__(self, parent, on_minus, on_plus, on_temp_set):
        super().__init__(parent, width=px(self.BASE_W), height=px(self.BASE_H), bg=BG, highlightthickness=0, bd=0)
        self.on_minus = on_minus
        self.on_plus = on_plus
        self.on_temp_set = on_temp_set
        
        self._last = None
        self.dragging = False
        self.drag_temp = None

        self.update_dimensions()

        # Mouse & Drag Binds
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._wheel)

    def update_dimensions(self):
        self.cx, self.cy, self.r = px(160), px(138), px(108)
        self.bx = (px(160 - 58), px(160 + 58))
        self.by, self.br = px(250), px(20)
        self.config(width=px(self.BASE_W), height=px(self.BASE_H))

    def _get_temp_from_xy(self, x, y):
        """Calculate target temperature based on angle relative to dial center."""
        if not self._last:
            return None
        tcurr, tmin, tmax, on, color, unit, mode_text, room_text = self._last
        if tmax <= tmin:
            return None

        dx = x - self.cx
        dy = -(y - self.cy)  # Invert Y for Cartesian angles
        angle = math.degrees(math.atan2(dy, dx)) % 360  # 0..360

        # Arc spans from 225 deg down to -45 deg (270 degrees total)
        # Normalizing angle to 0..270 offset starting at 225
        # 225 deg = 0 f, 180 = 45 deg f, 90 = 135 deg f, 0 = 225 deg f, 315 = 270 deg f
        if angle <= 225 and angle >= 0:
            arc_pos = 225 - angle
        elif angle > 225:
            arc_pos = (225 - angle) % 360
        else:
            return None

        if arc_pos > 270:
            return None  # Clicked in the bottom empty gap

        f = arc_pos / 270.0
        val = tmin + f * (tmax - tmin)
        return round(val)

    def _is_on_plus_minus(self, x, y):
        for idx, bx in enumerate(self.bx):
            if (x - bx) ** 2 + (y - self.by) ** 2 <= (self.br + 6) ** 2:
                return "minus" if idx == 0 else "plus"
        return None

    def _on_press(self, e):
        pm = self._is_on_plus_minus(e.x, e.y)
        if pm == "minus":
            self.on_minus()
            return
        elif pm == "plus":
            self.on_plus()
            return

        # Check if user clicked near dial arc
        dist = math.hypot(e.x - self.cx, e.y - self.cy)
        if abs(dist - self.r) < px(28):
            t_calc = self._get_temp_from_xy(e.x, e.y)
            if t_calc is not None:
                self.dragging = True
                self.drag_temp = t_calc
                self.redraw_current(temp_override=t_calc)

    def _on_drag(self, e):
        if self.dragging:
            t_calc = self._get_temp_from_xy(e.x, e.y)
            if t_calc is not None and t_calc != self.drag_temp:
                self.drag_temp = t_calc
                self.redraw_current(temp_override=t_calc)

    def _on_release(self, e):
        if self.dragging:
            self.dragging = False
            if self.drag_temp is not None:
                self.on_temp_set(self.drag_temp)
                self.drag_temp = None

    def _wheel(self, e):
        (self.on_plus if e.delta > 0 else self.on_minus)()
        return "break"

    def redraw(self):
        self.update_dimensions()
        if self._last:
            args = self._last
            self._last = None
            self.update_view(*args)

    def redraw_current(self, temp_override=None):
        if self._last:
            tcurr, tmin, tmax, on, color, unit, mode_text, room_text = self._last
            disp_temp = temp_override if temp_override is not None else tcurr
            self.delete("all")
            self._render(disp_temp, tmin, tmax, on, color, unit, mode_text, room_text)

    def update_view(self, temp, tmin, tmax, on, color, unit, mode_text, room_text):
        args = (temp, tmin, tmax, on, color, unit, mode_text, room_text)
        if args == self._last and not self.dragging:
            return
        self._last = args
        if not self.dragging:
            self.delete("all")
            self._render(temp, tmin, tmax, on, color, unit, mode_text, room_text)

    def _render(self, temp, tmin, tmax, on, color, unit, mode_text, room_text):
        cx, cy, r = self.cx, self.cy, self.r
        box = (cx - r, cy - r, cx + r, cy + r)
        lw = px(16)

        # Background track arc
        self.create_arc(*box, start=225, extent=-270, style="arc", width=lw, outline=TRACK)

        col = color if on else DIM
        if temp is not None and tmax > tmin:
            f = max(0.0, min(1.0, (temp - tmin) / (tmax - tmin)))
            if f > 0.002:
                # Active progress arc
                self.create_arc(*box, start=225, extent=-270 * f, style="arc", width=lw, outline=col)

            # Interactive handle knob
            a = math.radians(225 - 270 * f)
            kx, ky, kr = cx + r * math.cos(a), cy - r * math.sin(a), px(11)
            # Knob Shadow / Glow
            self.create_oval(kx - kr - 2, ky - kr - 2, kx + kr + 2, ky + kr + 2, fill="", outline=BORDER_LIGHT, width=1)
            self.create_oval(kx - kr, ky - kr, kx + kr, ky + kr, fill=FG, outline=col, width=max(2, px(4)))

        # Center Temperature Display
        big = f"{temp:g}\u00b0" if temp is not None else "--"
        self.create_text(cx, cy - px(12), text=big, fill=FG if on else MUT, font=(FONT, max(14, px(48)), "bold"))
        self.create_text(cx, cy + px(34), text=mode_text if on else "Off", fill=col if on else MUT, font=(FONT, max(8, px(11)), "bold"))
        self.create_text(cx, cy + px(58), text=room_text, fill=MUT, font=(FONT, max(7, px(9))))

        # - / + Circular Touch Buttons
        for x, sym in zip(self.bx, ("\u2212", "+")):
            self.create_oval(x - self.br, self.by - self.br, x + self.br, self.by + self.br, fill=CARD, outline=BORDER, width=1)
            self.create_text(x, self.by - px(1), text=sym, fill=FG, font=(FONT, max(10, px(18)), "bold"))


# ----------------------------------------------------------------------------- app
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Blue Star AC")
        self.root.configure(bg=BG)
        self.root.geometry("440x860")
        self.root.minsize(320, 420)

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.on_tk_error

        self.bridge = None
        self.tid = None
        self.pending = {}
        self.log_win = None
        self.canvas = None

        self._resize_job = None
        self.root.bind("<Configure>", self._on_window_resize)

        self.build_login()

    def _on_window_resize(self, event):
        if event.widget != self.root:
            return

        global S
        w, h = event.width, event.height
        new_s = max(0.55, min(w / BASE_W, h / BASE_H))

        if abs(new_s - S) > 0.01:
            S = new_s
            if self._resize_job:
                self.root.after_cancel(self._resize_job)
            self._resize_job = self.root.after(25, self.on_scale_change)

    def on_scale_change(self):
        if hasattr(self, 'dial') and self.dial and self.dial.winfo_exists():
            self.dial.redraw()

        for attr in ('g_mode', 'g_fan', 'g_extra', 'g_v', 'g_h'):
            grp = getattr(self, attr, None)
            if grp and grp.winfo_exists():
                grp.rescale()

        if hasattr(self, 'btn_pow') and self.btn_pow and self.btn_pow.winfo_exists():
            self.btn_pow.config(width=px(84), height=px(42))
            self.btn_pow.draw()

        if hasattr(self, 'btn_login') and self.btn_login and self.btn_login.winfo_exists():
            self.btn_login.config(height=px(46))
            self.btn_login.draw()

        if self.canvas and self.body:
            self._on_body_resize(None)

    # ---------------------------------------------------------------- housekeeping
    def on_tk_error(self, exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        print(msg)
        messagebox.showerror("Blue Star AC", msg[-1200:])

    def close(self):
        try:
            if self.bridge and self.bridge.client:
                self.bridge.client.loop_stop()
                self.bridge.client.disconnect()
        except Exception:
            pass
        self.root.destroy()

    def clear(self):
        self.canvas = None
        for w in self.root.winfo_children():
            w.destroy()

    def entry(self, parent, show=None):
        return tk.Entry(parent, bg=CARD, fg=FG, insertbackground=FG, relief="flat", show=show or "",
                        font=(FONT, max(8, px(11))), highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACC)

    # ---------------------------------------------------------------- login
    def build_login(self):
        self.clear()
        f = tk.Frame(self.root, bg=BG)
        f.pack(fill="both", expand=True, padx=px(36))

        tk.Label(f, text="Blue Star AC", bg=BG, fg=FG, font=(FONT, max(14, px(26)), "bold")).pack(pady=(px(70), px(4)))
        tk.Label(f, text="Sign in to control your smart air conditioner", bg=BG, fg=MUT, font=(FONT, max(8, px(10)))).pack(pady=(0, px(32)))

        tk.Label(f, text="PHONE (10 DIGITS) OR EMAIL", bg=BG, fg=MUT, font=(FONT, max(6, px(8)), "bold")).pack(anchor="w")
        saved = open(core.SAVED_ID_FILE).read().strip() if os.path.exists(core.SAVED_ID_FILE) else ""
        self.e_id = self.entry(f)
        self.e_id.insert(0, saved)
        self.e_id.pack(fill="x", ipady=px(7), pady=(px(4), px(16)))

        tk.Label(f, text="PASSWORD", bg=BG, fg=MUT, font=(FONT, max(6, px(8)), "bold")).pack(anchor="w")
        self.e_pw = self.entry(f, show="\u2022")
        self.e_pw.pack(fill="x", ipady=px(7), pady=(px(4), px(14)))

        self.remember = tk.IntVar(value=1 if saved else 0)
        tk.Checkbutton(f, text="Remember phone/email", variable=self.remember, bg=BG, fg=MUT,
                       selectcolor=CARD, activebackground=BG, activeforeground=FG, font=(FONT, max(7, px(9))), bd=0,
                       highlightthickness=0).pack(anchor="w")

        self.btn_login = Pill(f, "Sign in", self.do_login, height=46, size=11, bold=True, selected=True)
        self.btn_login.pack(fill="x", pady=(px(26), px(10)))

        self.lbl_login = tk.Label(f, text="", bg=BG, fg=RED, wraplength=px(340), justify="center", font=(FONT, max(7, px(9))))
        self.lbl_login.pack()

        (self.e_pw if saved else self.e_id).focus_set()
        self.root.bind("<Return>", lambda e: self.do_login())
        self.logging_in = False

    def do_login(self):
        if getattr(self, "logging_in", False):
            return
        aid, pw = self.e_id.get().strip(), self.e_pw.get()
        if not aid or not pw:
            self.lbl_login.config(text="Enter your phone/email and password.", fg=RED)
            return
        if self.remember.get():
            open(core.SAVED_ID_FILE, "w").write(aid)
        elif os.path.exists(core.SAVED_ID_FILE):
            os.remove(core.SAVED_ID_FILE)
        self.logging_in = True
        self.btn_login.set(text="Signing in...")
        self.lbl_login.config(text="", fg=MUT)

        def work():
            try:
                creds = core.login(aid, pw)
                things = core.fetch_devices(creds["session"])
                if not things:
                    raise RuntimeError("Signed in, but no AC was found on this account.")
                bridge = core.Bridge(creds, things)
                bridge._note(core.LAST_DISCOVERY)  # visible in the Log window
                bridge.pin = None
                bridge.start()
                self.root.after(0, lambda: self.after_login(bridge))
            except core.LoginError as e:
                msg = str(e)  # copy now: Python deletes "e" when this block ends
                self.root.after(0, lambda m=msg: self.login_failed(m))
            except SystemExit as e:
                msg = str(e.code)
                self.root.after(0, lambda m=msg: self.login_failed(m))
            except Exception as e:
                msg = f"Something went wrong while signing in: {type(e).__name__}: {e}"
                self.root.after(0, lambda m=msg: self.login_failed(m))

        threading.Thread(target=work, daemon=True).start()

    def login_failed(self, msg):
        self.logging_in = False
        self.btn_login.set(text="Sign in")
        self.lbl_login.config(text=msg, fg=RED)
        try:  # let the user retype straight away
            self.e_pw.delete(0, "end")
            self.e_pw.focus_set()
        except Exception:
            pass

    def after_login(self, bridge):
        try:
            self.bridge = bridge
            self.root.unbind("<Return>")
            self.tid = next(iter(bridge.things))
            self.build_main()
            self.poll()
        except Exception as e:
            msg = f"Signed in, but the screen could not be built: {type(e).__name__}: {e}"
            traceback.print_exc()
            self.bridge = None
            try:
                bridge.client.loop_stop()
            except Exception:
                pass
            self.build_login()
            self.login_failed(msg)

    # ---------------------------------------------------------------- main screen
    def build_main(self):
        self.clear()
        names = {tid: t["name"] for tid, t in self.bridge.things.items()}

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=px(20), pady=(px(16), px(4)))
        left = tk.Frame(head, bg=BG)
        left.pack(side="left")

        self.lbl_name = tk.Label(left, text=names[self.tid], bg=BG, fg=FG, font=(FONT, max(10, px(17)), "bold"))
        self.lbl_name.pack(anchor="w")

        srow = tk.Frame(left, bg=BG)
        srow.pack(anchor="w", pady=(px(2), 0))
        self.dot = tk.Canvas(srow, width=px(10), height=px(10), bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, px(6)))
        self.dot_item = self.dot.create_oval(1, 1, px(9), px(9), fill=AMBER, outline="")
        self.lbl_status = tk.Label(srow, text="", bg=BG, fg=MUT, font=(FONT, max(7, px(9))))
        self.lbl_status.pack(side="left")

        self.btn_pow = Pill(head, "OFF", self.toggle_power, height=40, size=10, bold=True, width=80, color=GREEN)
        self.btn_pow.pack(side="right")

        if len(names) > 1:
            drow = tk.Frame(self.root, bg=BG)
            drow.pack(fill="x", padx=px(18), pady=(px(6), 0))
            self.dev_pills = {}
            for i, (tid, n) in enumerate(names.items()):
                drow.columnconfigure(i, weight=1, uniform="d")
                p = Pill(drow, n, lambda tid=tid: self.switch(tid), height=32, size=9, selected=(tid == self.tid))
                p.grid(row=0, column=i, sticky="ew", padx=px(3))
                self.dev_pills[tid] = p

        # scrollable body
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, pady=(px(4), 0))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.sbar = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.sbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.body = tk.Frame(self.canvas, bg=BG)
        self.body_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.body.bind("<Configure>", self._on_body_resize)
        self.root.bind_all("<MouseWheel>", self.on_wheel)
        self.build_device()

    def _on_canvas_resize(self, e):
        self.canvas.itemconfigure(self.body_id, width=e.width)
        self._on_body_resize(None)

    def _on_body_resize(self, e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self.canvas.winfo_height()
        if need:
            self.sbar.grid(row=0, column=1, sticky="ns")
        else:
            self.sbar.grid_remove()
            self.canvas.yview_moveto(0)

    def on_wheel(self, e):
        if self.canvas and self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def switch(self, tid):
        self.tid = tid
        self.lbl_name.config(text=self.bridge.things[tid]["name"])
        for t, p in self.dev_pills.items():
            p.set(selected=(t == tid))
        self.pending.clear()
        self.build_device()

    def build_device(self):
        for w in self.body.winfo_children():
            w.destroy()
        t = self.bridge.things[self.tid]
        cfg = t["config"] if isinstance(t["config"], dict) else {}
        self.tmin = num(cfg.get("min_temp"), 16)
        self.tmax = num(cfg.get("max_temp"), 30)
        b = self.body
        pad = dict(fill="x", padx=px(18))

        # Dial Widget with Drag/Slide Support
        self.dial = Dial(b, lambda: self.bump(-1), lambda: self.bump(+1), on_temp_set=self.set_temp)
        self.dial.pack(pady=(px(2), 0))

        # Exact temperature row
        ex = tk.Frame(b, bg=BG)
        ex.pack(pady=(0, px(8)))
        tk.Label(ex, text="Exact Temp", bg=BG, fg=MUT, font=(FONT, max(7, px(9)))).pack(side="left", padx=(0, px(8)))
        self.e_temp = self.entry(ex)
        self.e_temp.config(width=4, justify="center")
        self.e_temp.pack(side="left", ipady=px(3))
        self.e_temp.bind("<Return>", lambda e: self.set_typed_temp())
        Pill(ex, "Set", self.set_typed_temp, height=30, size=8, width=54).pack(side="left", padx=px(8))

        # Controls & Cards
        self.modes, fans = core.build_options(cfg)
        self.g_mode = self.g_fan = self.g_v = self.g_h = None

        if self.modes:
            self.g_mode = PillGroup(b, "Mode", self.modes, lambda k: self.send({"mode": {"value": int(k)}}, {"mode": int(k)}), per_row=3)
            self.g_mode.pack(pady=(px(6), 0), **pad)

        if fans:
            self.g_fan = PillGroup(b, "Fan speed", fans, lambda k: self.send({"fspd": int(k)}, {"fspd": int(k)}), per_row=3)
            self.g_fan.pack(pady=(px(8), 0), **pad)
        else:
            ff = SectionCard(b, "Fan speed", per_row=1)
            ff.pack(pady=(px(8), 0), **pad)
            row = tk.Frame(ff, bg=CARD)
            row.pack(anchor="w", padx=px(12), pady=(0, px(10)))
            self.e_fan = self.entry(row)
            self.e_fan.config(width=5, justify="center")
            self.e_fan.pack(side="left", ipady=px(3))
            Pill(row, "Set", lambda: self.send({"fspd": int(num(self.e_fan.get(), 0))}), height=30, size=8, width=54).pack(side="left", padx=px(8))

        self.g_extra = PillGroup(b, "Extras", {"turbo": "Turbo", "sleep": "Sleep", "display": "Display"},
                                 self.toggle, per_row=3, color=ACC)
        self.g_extra.pack(pady=(px(8), 0), **pad)

        vs, hs = core.swing_options(cfg, "vswing"), core.swing_options(cfg, "hswing")
        if vs:
            self.g_v = PillGroup(b, "Vertical louver", vs, lambda k: self.send({"vswing": int(k)}, {"vswing": int(k)}), per_row=3)
            self.g_v.pack(pady=(px(8), 0), **pad)
        if hs:
            self.g_h = PillGroup(b, "Horizontal louver", hs, lambda k: self.send({"hswing": int(k)}, {"hswing": int(k)}), per_row=3)
            self.g_h.pack(pady=(px(8), 0), **pad)

        foot = tk.Frame(b, bg=BG)
        foot.pack(pady=(px(20), px(16)))
        for i, (txt, cmd) in enumerate((("Refresh", lambda: self.bridge.request_state(self.tid)),
                                        ("Log", self.show_log), ("Sign out", self.sign_out))):
            lb = tk.Label(foot, text=txt, bg=BG, fg=MUT, font=(FONT, max(7, px(9))), cursor="hand2")
            lb.pack(side="left", padx=px(14))
            lb.bind("<Button-1>", lambda e, c=cmd: c())
            lb.bind("<Enter>", lambda e, w=lb: w.config(fg=FG))
            lb.bind("<Leave>", lambda e, w=lb: w.config(fg=MUT))

        self.root.after(50, lambda: self._on_body_resize(None))

    # ---------------------------------------------------------------- actions
    def send(self, desired, pending=None):
        try:
            self.bridge.set_desired(self.tid, desired)
            for k, v in (pending or {}).items():
                self.pending[k] = (v, time.time() + 4)
        except Exception as e:
            messagebox.showerror("Blue Star AC", f"Could not send command:\n{e}")

    def state(self):
        return self.bridge.state.get(self.tid, {})

    def shown(self, key):
        """Value to display: a just-requested value (for 4s) else what the AC reported."""
        p = self.pending.get(key)
        if p and time.time() < p[1]:
            return p[0]
        self.pending.pop(key, None)
        return self.state().get(key)

    def toggle_power(self):
        on = int(num(self.shown("pow"), 0)) == 1
        self.send({"pow": 0 if on else 1}, {"pow": 0 if on else 1})

    def toggle(self, key):
        on = int(num(self.shown(key), 0)) == 1
        self.send({key: 0 if on else 1}, {key: 0 if on else 1})

    def set_temp(self, val):
        val = max(self.tmin, min(self.tmax, val))
        self.send({"stemp": val}, {"stemp": val})

    def bump(self, d):
        cur = num(self.shown("stemp"), 24)
        new = max(self.tmin, min(self.tmax, cur + d))
        self.set_temp(new)

    def set_typed_temp(self):
        v = num(self.e_temp.get())
        if v is None:
            return
        self.set_temp(v)
        self.e_temp.delete(0, "end")

    def sign_out(self):
        try:
            self.bridge.client.loop_stop()
            self.bridge.client.disconnect()
        except Exception:
            pass
        self.bridge = None
        self.root.unbind_all("<MouseWheel>")
        self.build_login()

    # ---------------------------------------------------------------- log window
    def show_log(self):
        if self.log_win and self.log_win.winfo_exists():
            self.log_win.lift()
            return
        self.log_win = tk.Toplevel(self.root)
        self.log_win.title("Blue Star AC - log")
        self.log_win.configure(bg=BG)
        self.log_win.geometry(f"{px(780)}x{px(380)}")
        self.log_text = tk.Text(self.log_win, wrap="word", font=("Consolas", 9), bg=CARD, fg=FG, relief="flat",
                                insertbackground=FG, bd=0, padx=px(8), pady=px(8))
        self.log_text.pack(fill="both", expand=True)
        self._last_log = None

    # ---------------------------------------------------------------- refresh loop
    def poll(self):
        if not self.bridge:
            return
        try:
            self.refresh()
        except Exception:
            traceback.print_exc()
        self.root.after(700, self.poll)

    def mode_color_and_name(self):
        mode = self.shown("mode")
        if isinstance(mode, dict):
            mode = mode.get("value")
        key = key_of(num(mode))
        name = self.modes.get(key, "") if key is not None else ""
        low = name.lower()
        for frag, col in MODE_COLORS:
            if frag in low:
                return col, name, key
        return ACC, name, key

    def refresh(self):
        st = self.state()
        seen = self.bridge.last_seen.get(self.tid)
        if not self.bridge.connected:
            self.dot.itemconfigure(self.dot_item, fill=AMBER)
            self.lbl_status.config(text="Connecting to cloud...")
        elif not seen:
            self.dot.itemconfigure(self.dot_item, fill=AMBER)
            self.lbl_status.config(text="Connected \u00b7 waiting for AC...")
        else:
            self.dot.itemconfigure(self.dot_item, fill=GREEN)
            self.lbl_status.config(text=f"Online \u00b7 updated {seen}")

        unit = "\u00b0F" if int(num(st.get("displayunit"), 0)) == 1 else "\u00b0C"
        on = int(num(self.shown("pow"), 0)) == 1
        color, mode_name, mode_key = self.mode_color_and_name()
        fan_txt = ""
        if self.g_fan:
            fk = key_of(num(self.shown("fspd")))
            fan_name = self.g_fan.pills[fk].text if fk in self.g_fan.pills else ""
            fan_txt = f"  \u00b7  Fan {fan_name}" if fan_name else ""
        rv = num(st.get("ctemp"))
        self.dial.update_view(num(self.shown("stemp")), self.tmin, self.tmax, on, color, unit,
                              (mode_name or "On") + fan_txt, f"Room {rv:g}{unit}" if rv is not None else "Room --")
        self.btn_pow.set(selected=on, text="ON" if on else "OFF")

        if self.g_mode:
            self.g_mode.show(mode_key, color)
        if self.g_fan:
            self.g_fan.show(key_of(num(self.shown("fspd"))))
        self.g_extra.show_many({k for k in ("turbo", "sleep", "display") if int(num(self.shown(k), 0)) == 1})
        if self.g_v:
            self.g_v.show(key_of(num(self.shown("vswing"))))
        if self.g_h:
            self.g_h.show(key_of(num(self.shown("hswing"))))

        if self.log_win and self.log_win.winfo_exists():
            txt = "\n".join(self.bridge.log)
            if txt != getattr(self, "_last_log", None):
                self._last_log = txt
                self.log_text.delete("1.0", "end")
                self.log_text.insert("end", txt)
                self.log_text.see("end")


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = sys.stdout
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    if getattr(core, "CORE_VERSION", 0) < 3:
        root.withdraw()
        messagebox.showerror(
            "Blue Star AC",
            "The file bluestar_pc.py next to this app is an OLD version.\n\n"
            "Replace it with the newest bluestar_pc.py (the one that contains 'CORE_VERSION = 3') "
            "and start the app again.")
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()