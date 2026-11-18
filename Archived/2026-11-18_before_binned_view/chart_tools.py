"""Chart toolbar: drag modes (select window / zoom x / zoom y / cursors), view history, save.

Standalone: knows nothing about the analyzer. The app supplies callbacks and calls attach()
after every redraw, because ax.clear() throws away the drag selector.
"""
import math
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
from matplotlib.widgets import SpanSelector

MODES = ("select", "zoom_x", "zoom_y", "cursors")
MODE_LABELS = (("select", "Select window"), ("zoom_x", "Zoom \u2194"), ("zoom_y", "Zoom \u2195"),
               ("cursors", "Cursors"))
KEYS = {"w": "select", "x": "zoom_x", "y": "zoom_y", "c": "cursors"}
HISTORY_MAX = 50
MIN_DRAG_PX = 3
ACCENT = "#0f766e"       # same as the app accent; passed to the selector and the active-mode button
CARD = "#ffffff"
MODE_STYLE = "Mode.Toolbutton"
GRAB_PX = 6                # cursor grab distance
DASH = "\u2013"
COPY_HEADER = ("Trace\tt_A (s)\tV_A (mV)\tt_B (s)\tV_B (mV)\t\u0394V (mV)\t\u0394t (s)\t1/\u0394t (Hz)"
               "\tY1 (mV)\tY2 (mV)\t\u0394Y (mV)")
CURSOR_COLORS = {"A": "#c2410c", "B": "#1d4ed8", "Y1": "#7c3aed", "Y2": "#047857"}
LEVELS = ("Y1", "Y2")       # horizontal (amplitude) cursors
BAD_BG = "#fecaca"         # invalid typed value
OK_BG = "#ffffff"


def format_dt(dt):
    """'\u0394t 7.038 ms (1/\u0394t = 142.1 Hz)'; ms below 1 s else s; kHz above 10 kHz."""
    a = abs(dt)
    txt = f"\u0394t {dt * 1e3:.3f} ms" if a < 1 else f"\u0394t {dt:.6f} s"
    if a == 0:
        return f"{txt} (1/\u0394t —)"  # em dash, as in the spec
    f = 1.0 / a
    return f"{txt} (1/\u0394t = {f / 1e3:.3f} kHz)" if f > 10_000 else f"{txt} (1/\u0394t = {f:.1f} Hz)"


def sample_at(data, t):
    """Value of the sample nearest to time t, or None when t is outside the trace's time range."""
    if data.n == 0 or not (data.t_start <= t <= data.t_end):
        return None
    i = data.index_range(t, data.t_end)[0]
    cand = np.array(sorted({min(max(i - 1, 0), data.n - 1), min(i, data.n - 1)}))
    j = int(cand[int(np.argmin(np.abs(data.times_at(cand) - t)))])
    return float(data.values_at(np.array([j]))[0])


class ChartTools:
    def __init__(self, parent, ax, canvas, *, on_window_selected, on_reset, autoscale_y,
                 get_selected_trace=lambda: None, get_shown_traces=lambda: [],
                 label_of=lambda f: f.get("name", ""), on_mode_changed=None):
        self._on_mode_changed = on_mode_changed
        self.ax = ax
        self.canvas = canvas
        self._on_window_selected = on_window_selected
        self._on_reset = on_reset
        self._autoscale_y = autoscale_y
        self._mode = "select"
        self._history = []              # previous (xlim, ylim) views, oldest first
        self._selector = None           # kept referenced so it is not garbage-collected
        self._get_selected = get_selected_trace
        self._get_shown = get_shown_traces
        self._label_of = label_of
        self.cursor_times = None        # (tA, tB) once the cursors have been placed
        self._cursor_lines = []
        self._dragging = None           # "A" / "B" while a cursor is being dragged
        self._bg = None                 # cached background while dragging (blitting)
        self._markers = []              # animated "off-screen cursor" labels (cursors mode only)
        self.cursor_levels = None       # (Y1, Y2) in mV once the level cursors have been placed
        self._level_lines = []
        self._level_markers = []
        self._axis_entries = None       # {"axis", "lo", "hi"} while a typed axis range is open

        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self._mode_var = tk.StringVar(master=parent, value=self._mode)
        st = ttk.Style(master=parent)
        st.configure(MODE_STYLE, background=CARD, padding=(8, 3))
        st.map(MODE_STYLE, background=[("selected", ACCENT), ("active", "#e6f2f1")],
               foreground=[("selected", "#ffffff")])
        self.mode_buttons = {}
        for mode, text in MODE_LABELS:
            b = ttk.Radiobutton(self.frame, text=text, value=mode, variable=self._mode_var,
                                style=MODE_STYLE, command=lambda m=mode: self.set_mode(m))
            b.pack(side=tk.LEFT, padx=(0, 4))
            self.mode_buttons[mode] = b
        self.back_button = ttk.Button(self.frame, text="Back", command=self.back)
        self.back_button.pack(side=tk.LEFT, padx=(8, 4))
        self._update_back()
        ttk.Button(self.frame, text="Reset zoom", command=self.reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.frame, text="Save image\u2026", command=self.save_image).pack(side=tk.LEFT, padx=4)

        w = canvas.get_tk_widget()
        w.bind("<Button-1>", lambda e: w.focus_set(), add="+")
        for key, mode in KEYS.items():
            for k in (key, key.upper()):
                w.bind(f"<KeyPress-{k}>", lambda e, m=mode: self.set_mode(m))
        w.bind("<BackSpace>", lambda e: self.back())
        w.bind("<Home>", lambda e: self.reset())
        for k in ("r", "R"):
            w.bind(f"<KeyPress-{k}>", lambda e: self.cursors_to_view())

        self.readout_frame = ttk.Frame(parent, style="Card.TFrame")
        self._readout_var = tk.StringVar(master=parent, value="")
        self._level_var = tk.StringVar(master=parent, value="")
        # one label per line, always packed: the chart height never changes when the text appears
        for var in (self._readout_var, self._level_var):
            ttk.Label(self.readout_frame, textvariable=var, style="Muted.Card.TLabel"
                      ).pack(side=tk.TOP, fill=tk.X)
        controls = ttk.Frame(self.readout_frame, style="Card.TFrame")      # own row: the readout text is long
        controls.pack(side=tk.TOP, fill=tk.X)
        self.copy_button = ttk.Button(controls, text="Copy cursor values",
                                      command=self.copy_cursor_values)
        self.copy_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.view_button = ttk.Button(controls, text="Cursors to view", command=self.cursors_to_view)
        self.view_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.cursor_entries = {}
        for which in ("Y2", "Y1", "B", "A"):   # packed right to left: A [ ] B [ ] Y1 [ ] Y2 [ ] Cursors to view ...
            e = tk.Entry(controls, width=11, relief=tk.SOLID, bd=1, bg=OK_BG, state="disabled",
                         disabledbackground="#f3f4f6")
            e.pack(side=tk.RIGHT, padx=(2, 0))
            ttk.Label(controls, text=which, style="Muted.Card.TLabel").pack(side=tk.RIGHT, padx=(8, 0))
            e.bind("<Return>", lambda ev, wh=which: self._apply_cursor_entry(wh))
            e.bind("<Key>", lambda ev: self._clear_bad(ev))
            e.bind("<FocusOut>", lambda ev: self.frame.after_idle(self.refresh_readout))
            self.cursor_entries[which] = e
        canvas.mpl_connect("resize_event", lambda ev: self.close_axis_entries())   # boxes would be misplaced
        for name, handler in (("draw_event", self._on_draw), ("button_press_event", self._on_press),
                              ("motion_notify_event", self._on_motion),
                              ("button_release_event", self._on_release)):
            canvas.mpl_connect(name, handler)
        self.attach()

    # ---- state ----
    @property
    def mode(self):
        return self._mode

    @property
    def can_go_back(self):
        return bool(self._history)

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self._mode = mode
        self._mode_var.set(mode)
        self.attach()
        self.canvas.draw_idle()
        if self._on_mode_changed:
            self._on_mode_changed(mode)

    # ---- drag selector ----
    def attach(self):
        """(Re)create the drag selector on the current axes; call after every redraw."""
        self.close_axis_entries()
        if self._selector is not None:
            self._selector.disconnect_events()
        self._selector = None
        self._make_selector()
        self._make_cursor_lines()
        self.refresh_readout()

    def _make_selector(self):
        mode = self._mode
        if mode == "cursors":           # cursors have their own press/motion/release handlers
            self._selector = None
            return
        direction = "vertical" if mode == "zoom_y" else "horizontal"
        self._selector = SpanSelector(self.ax, self._on_drag, direction, useblit=True,
                                      props=dict(alpha=0.2, facecolor=ACCENT), interactive=False)

    def detach(self):
        """Drop the drag selector and the history (the axes no longer show a trace view)."""
        self.close_axis_entries()
        if self._selector is not None:
            self._selector.disconnect_events()
        self._selector = None
        self._cursor_lines = []
        self._markers = []
        self._dragging = None
        self._bg = None
        self.clear_history()
        self.refresh_readout()

    def _on_drag(self, lo, hi):
        mode = self._mode
        axis = 1 if mode == "zoom_y" else 0
        p0 = self.ax.transData.transform((lo, lo) if axis else (lo, 0))[axis]
        p1 = self.ax.transData.transform((hi, hi) if axis else (hi, 0))[axis]
        if abs(p1 - p0) < MIN_DRAG_PX or hi <= lo:
            return
        if mode == "select":
            self._on_window_selected(lo, hi)
        elif mode == "zoom_x":
            self.zoom_x(lo, hi)
        elif mode == "zoom_y":
            self.zoom_y(lo, hi)

    # ---- measurement cursors ----
    def _default_times(self):
        lo, hi = self.ax.get_xlim()
        return lo + (hi - lo) / 3, lo + 2 * (hi - lo) / 3

    def _make_cursor_lines(self):
        """(Re)create the A/B lines; ax.clear() removed the old ones. Only visible in cursors mode."""
        for ln in self._cursor_lines:       # ax.clear() may already have dropped them
            try:
                ln.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._cursor_lines = []
        for t in self._markers + self._level_lines + self._level_markers:
            try:
                t.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._markers = []
        self._level_lines = []
        self._level_markers = []
        if self._mode != "cursors":
            return
        if self.cursor_levels is None:
            self.cursor_levels = self._default_levels()
        for which, y in zip(LEVELS, self.cursor_levels):
            self._level_lines.append(self.ax.axhline(y, color=CURSOR_COLORS[which], lw=1.3, ls=(0, (6, 3)),
                                                     zorder=4, animated=True))
            self._level_markers.append(self.ax.text(0.5, 0.5, "", transform=self.ax.transAxes,
                                                    color=CURSOR_COLORS[which], fontsize=9, fontweight="bold",
                                                    ha="center", zorder=5, animated=True, visible=False))
        if self.cursor_times is None:
            self.cursor_times = self._default_times()
        for which, t in zip("AB", self.cursor_times):      # animated: drawn by blit, never by a render
            self._cursor_lines.append(self.ax.axvline(t, color=CURSOR_COLORS[which], lw=1.3, zorder=4,
                                                      animated=True))
        for which in "AB":
            self._markers.append(self.ax.text(0, 0.97, "", transform=self.ax.transAxes, color=CURSOR_COLORS[which],
                                              fontsize=9, fontweight="bold", va="top", zorder=5, animated=True,
                                              visible=False))
        self._update_markers()

    def _update_markers(self):
        """Show '\u25c0 A' / 'B \u25b6' at the chart edge for cursors outside the current x-limits."""
        if not self._markers or self.cursor_times is None:
            self._update_level_markers()
            return
        lo, hi = sorted(self.ax.get_xlim())
        used = {"L": 0, "R": 0}
        for which, t, mk in zip("AB", self.cursor_times, self._markers):
            side = "L" if t < lo else "R" if t > hi else None
            mk.set_visible(side is not None)
            if side is None:
                continue
            mk.set_text(f"\u25c0 {which}" if side == "L" else f"{which} \u25b6")
            mk.set_horizontalalignment("left" if side == "L" else "right")
            mk.set_position((0.005 if side == "L" else 0.995, 0.97 - 0.07 * used[side]))
            used[side] += 1
        self._update_level_markers()

    def _update_level_markers(self):
        """Show 'Y1 \u25b2' / 'Y1 \u25bc' at the top/bottom centre for levels outside the y-limits
        (the corners belong to the A/B markers)."""
        if not self._level_markers or self.cursor_levels is None:
            return
        lo, hi = sorted(self.ax.get_ylim())
        used = {"T": 0, "B": 0}
        for which, y, mk in zip(LEVELS, self.cursor_levels, self._level_markers):
            side = "B" if y < lo else "T" if y > hi else None
            mk.set_visible(side is not None)
            if side is None:
                continue
            mk.set_text(f"{which} \u25b2" if side == "T" else f"{which} \u25bc")
            mk.set_verticalalignment("top" if side == "T" else "bottom")
            mk.set_position((0.5 + 0.08 * used[side], 0.97 if side == "T" else 0.03))
            used[side] += 1

    def _on_draw(self, _ev=None):
        """After every full render: cache the background (without cursors), then blit the cursors."""
        if not self._cursor_lines:
            self._bg = None
            return
        try:
            self._update_markers()
            self._bg = self.canvas.copy_from_bbox(self.ax.bbox)
            self._blit_cursors(restore=False)
        except Exception:
            self._bg = None

    def _blit_cursors(self, restore=True):
        if restore:
            self.canvas.restore_region(self._bg)
        for ln in self._cursor_lines + self._markers + self._level_lines + self._level_markers:
            self.ax.draw_artist(ln)
        self.canvas.blit(self.ax.bbox)

    def set_cursor(self, which, t, _blit=False):
        if which not in ("A", "B"):
            raise ValueError(f"unknown cursor {which!r}")
        ta, tb = self.cursor_times or self._default_times()
        self.cursor_times = (float(t), tb) if which == "A" else (ta, float(t))
        if self._cursor_lines:
            line = self._cursor_lines["AB".index(which)]
            line.set_xdata([float(t), float(t)])
            self._update_markers()
            if _blit and self._bg is not None:
                self._blit_cursors()
            else:
                self.canvas.draw_idle()
        self.refresh_readout()

    def _default_levels(self):
        lo, hi = sorted(self.ax.get_ylim())
        return lo + (hi - lo) / 3, lo + 2 * (hi - lo) / 3

    def set_level(self, which, y, _blit=False):
        if which not in LEVELS:
            raise ValueError(f"unknown level cursor {which!r}")
        y1, y2 = self.cursor_levels or self._default_levels()
        self.cursor_levels = (float(y), y2) if which == "Y1" else (y1, float(y))
        if self._level_lines:
            self._level_lines[LEVELS.index(which)].set_ydata([float(y), float(y)])
            self._update_level_markers()
            if _blit and self._bg is not None:
                self._blit_cursors()
            else:
                self.canvas.draw_idle()
        self.refresh_readout()

    def level_text(self):
        if self.cursor_levels is None:
            return ""
        y1, y2 = self.cursor_levels
        return f"Y1: {y1:.4f} mV | Y2: {y2:.4f} mV | \u0394Y (Y2\u2212Y1) {y2 - y1:.4f} mV"

    def cursors_to_view(self):
        """Move A/B to 1/3 and 2/3 of the visible time range, Y1/Y2 likewise in amplitude."""
        if self._mode != "cursors":
            return
        lo, hi = sorted(self.ax.get_xlim())
        self.set_cursor("A", lo + (hi - lo) / 3)
        self.set_cursor("B", lo + 2 * (hi - lo) / 3)
        y1, y2 = self._default_levels()
        self.set_level("Y1", y1)
        self.set_level("Y2", y2)

    def _on_press(self, ev):
        self._dragging = None
        if self._axis_entries is not None:
            self.close_axis_entries()           # any click cancels an open typed range
        if ev.button == 1 and ev.dblclick and ev.x is not None:
            axis = self._axis_hit(ev)
            if axis:
                self.open_axis_entries(axis)
            return
        if self._mode != "cursors" or ev.button != 1 or not self._cursor_lines or ev.x is None:
            return
        if not self.ax.bbox.contains(ev.x, ev.y):
            return
        best = None
        for which, t in zip("AB", self.cursor_times):
            d = abs(self.ax.transData.transform((t, 0))[0] - ev.x)
            if d <= GRAB_PX and (best is None or d < best[0]):
                best = (d, which)
        for which, y in zip(LEVELS, self.cursor_levels or ()):
            d = abs(self.ax.transData.transform((0, y))[1] - ev.y)
            if d <= GRAB_PX and (best is None or d < best[0]):     # ties keep A/B
                best = (d, which)
        self._dragging = best[1] if best else None     # no render: the cached background is reused

    def _on_motion(self, ev):
        if self._dragging is None or self._mode != "cursors" or ev.x is None:
            return
        x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
        if self._dragging in LEVELS:
            self.set_level(self._dragging, y, _blit=True)
        else:
            self.set_cursor(self._dragging, x, _blit=True)

    def _on_release(self, ev):
        self._dragging = None

    def _readout_trace(self):
        shown = self._get_shown()
        if not shown:
            return None
        sel = self._get_selected()
        return sel if sel is not None and any(sel is f for f in shown) else shown[0]

    def _measure(self, f):
        """(tA, vA, tB, vB) with None for a value outside the trace's time range."""
        ta, tb = self.cursor_times
        return ta, sample_at(f["data"], ta), tb, sample_at(f["data"], tb)

    def readout_text(self):
        f = self._readout_trace()
        if f is None or self.cursor_times is None:
            return ""
        ta, va, tb, vb = self._measure(f)
        fmt = lambda v: DASH if v is None else f"{v:.4f}"
        dv = DASH if va is None or vb is None else f"{vb - va:.4f}"
        return (f"{self._label_of(f)}   A: {ta:.6f} s, {fmt(va)} mV | B: {tb:.6f} s, {fmt(vb)} mV | "
                f"{format_dt(tb - ta)} | \u0394V {dv} mV")

    def refresh_readout(self):
        on = self._mode == "cursors"
        self._readout_var.set(self.readout_text() if on else "")
        self._level_var.set(self.level_text() if on else "")
        self.copy_button.state(["!disabled" if on else "disabled"])
        self.view_button.state(["!disabled" if on else "disabled"])
        for which, e in self.cursor_entries.items():
            v = self._entry_value(which)
            self._set_cursor_entry(which, v if on and v is not None else "")
            e.config(state="normal" if on else "disabled")

    def _set_cursor_entry(self, which, text, force=False):
        e = self.cursor_entries[which]
        try:
            editing = e.focus_get() is e
        except KeyError:
            editing = False
        if editing and not force:
            return                              # never overwrite what the user is typing
        was = str(e.cget("state"))
        e.config(state="normal")
        e.delete(0, tk.END)
        e.insert(0, text)
        e.config(state=was, bg=OK_BG)

    @staticmethod
    def _parse_number(text):
        try:
            v = float(text.strip())
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def _entry_value(self, which):
        """Text for a cursor's entry box: time (6 decimals) for A/B, level (4 decimals) for Y1/Y2."""
        if which in LEVELS:
            return None if self.cursor_levels is None else f"{self.cursor_levels[LEVELS.index(which)]:.4f}"
        return None if self.cursor_times is None else f"{self.cursor_times['AB'.index(which)]:.6f}"

    def _apply_cursor_entry(self, which):
        e = self.cursor_entries[which]
        v = self._parse_number(e.get())
        if v is None or self._mode != "cursors":
            e.config(bg=BAD_BG)
            return
        e.config(bg=OK_BG)
        if which in LEVELS:
            self.set_level(which, v, _blit=True)
        else:
            self.set_cursor(which, v, _blit=True)
        self._set_cursor_entry(which, self._entry_value(which), force=True)

    @staticmethod
    def _clear_bad(ev):
        if ev.keysym not in ("Return", "Escape", "Tab"):
            ev.widget.config(bg=OK_BG)

    # ---- typed axis ranges ----
    def _axis_hit(self, ev):
        """'x' / 'y' when ev is over the tick-label area of that axis (outside the plot box), else None."""
        try:
            r = self.canvas.get_renderer()
        except Exception:
            return None
        if self.ax.bbox.contains(ev.x, ev.y):
            return None
        for name, axis in (("x", self.ax.xaxis), ("y", self.ax.yaxis)):
            bb = axis.get_tightbbox(r)
            if bb is not None and bb.contains(ev.x, ev.y):
                return name
        return None

    def open_axis_entries(self, axis):
        """Two boxes on the canvas at the ends of the axis, prefilled with its limits."""
        self.close_axis_entries()
        w = self.canvas.get_tk_widget()
        h = self.ax.figure.bbox.height
        b = self.ax.bbox
        lims = self.ax.get_xlim() if axis == "x" else self.ax.get_ylim()
        boxes = {}
        for key, v in zip(("lo", "hi"), lims):
            e = tk.Entry(w, width=9, relief=tk.SOLID, bd=1, bg=OK_BG, highlightthickness=1,
                         highlightcolor=ACCENT)
            e.insert(0, f"{v:.6g}")
            e.bind("<Return>", lambda ev: self._apply_axis_entries())
            e.bind("<Escape>", lambda ev: self.close_axis_entries())
            e.bind("<FocusOut>", lambda ev: self._on_entry_focus_out())
            e.bind("<Key>", lambda ev: self._clear_bad(ev))
            boxes[key] = e
        if axis == "x":
            y = h - b.y0 + 2
            boxes["lo"].place(x=b.x0, y=y, anchor="nw")
            boxes["hi"].place(x=b.x1, y=y, anchor="ne")
        else:
            boxes["lo"].place(x=b.x0 - 2, y=h - b.y0, anchor="se")     # right edge just left of the plot
            boxes["hi"].place(x=b.x0 - 2, y=h - b.y1, anchor="ne")
        self._axis_entries = dict(axis=axis, **boxes)
        boxes["lo"].focus_set()
        boxes["lo"].select_range(0, tk.END)

    def close_axis_entries(self):
        ent, self._axis_entries = self._axis_entries, None
        if ent:
            for k in ("lo", "hi"):
                ent[k].destroy()

    def _on_entry_focus_out(self):
        self.frame.after_idle(self._cancel_if_unfocused)

    def _cancel_if_unfocused(self):
        ent = self._axis_entries
        if ent is None:
            return
        try:
            f = self.frame.focus_get()
        except KeyError:
            f = None
        if f is not ent["lo"] and f is not ent["hi"]:
            self.close_axis_entries()

    def _apply_axis_entries(self):
        ent = self._axis_entries
        if ent is None:
            return
        lo, hi = (self._parse_number(ent[k].get()) for k in ("lo", "hi"))
        bad = {"lo": lo is None, "hi": hi is None}
        if lo is not None and hi is not None and lo >= hi:
            bad = {"lo": True, "hi": True}
        for k in ("lo", "hi"):
            ent[k].config(bg=BAD_BG if bad[k] else OK_BG)
        if bad["lo"] or bad["hi"]:
            return
        axis = ent["axis"]
        self.close_axis_entries()
        if axis == "x":
            self.zoom_x(lo, hi, fit_y=False)
        else:
            self.zoom_y(lo, hi)

    def cursor_table(self):
        shown = self._get_shown()
        if not shown or self.cursor_times is None:
            return ""
        rows = [COPY_HEADER]
        num = lambda v: "" if v is None else f"{v:.4f}"
        y1, y2 = self.cursor_levels or self._default_levels()
        levels = [f"{y1:.4f}", f"{y2:.4f}", f"{y2 - y1:.4f}"]
        for f in shown:
            ta, va, tb, vb = self._measure(f)
            dt = tb - ta
            dv = "" if va is None or vb is None else f"{vb - va:.4f}"
            hz = "" if dt == 0 else f"{1 / abs(dt):.1f}"
            rows.append("\t".join([self._label_of(f), f"{ta:.6f}", num(va), f"{tb:.6f}", num(vb),
                                   dv, f"{dt:.6f}", hz, *levels]))
        return "\n".join(rows)

    def copy_cursor_values(self):
        text = self.cursor_table()
        if text:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)

    # ---- view changes ----
    def _push(self):
        self._history.append((tuple(self.ax.get_xlim()), tuple(self.ax.get_ylim())))
        del self._history[:-HISTORY_MAX]
        self._update_back()

    def _update_back(self):
        self.back_button.state(["!disabled" if self._history else "disabled"])

    def zoom_x(self, x0, x1, fit_y=True):
        """Zoom the x axis to [x0, x1] and (fit_y) fit y to the points drawn there (via autoscale_y)."""
        if x1 <= x0:
            return
        self._push()
        self.ax.set_xlim(x0, x1)
        fit = self._autoscale_y(x0, x1) if fit_y else None
        if fit is not None and fit[1] > fit[0]:
            self.ax.set_ylim(*fit)
        self.canvas.draw_idle()

    def zoom_y(self, y0, y1):
        if y1 <= y0:
            return
        self._push()
        self.ax.set_ylim(y0, y1)
        self.canvas.draw_idle()

    def back(self):
        if not self._history:
            return
        xl, yl = self._history.pop()
        self.ax.set_xlim(xl)
        self.ax.set_ylim(yl)
        self._update_back()
        self.canvas.draw_idle()

    def clear_history(self):
        self._history.clear()
        self._update_back()

    def reset(self):
        self.clear_history()
        self._on_reset()

    def save_image(self):
        path = filedialog.asksaveasfilename(
            parent=self.frame, title="Save chart image", defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")])
        if path:
            for ln in self._cursor_lines:       # animated artists are skipped by savefig
                ln.set_animated(False)
            try:
                self.ax.figure.savefig(path, dpi=150, facecolor=self.ax.figure.get_facecolor())
            finally:
                for ln in self._cursor_lines:
                    ln.set_animated(True)
