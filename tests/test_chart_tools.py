"""Chart toolbar: modes, axis zoom, Back.  Run: python -m unittest discover -s tests"""
import gc
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import chart_tools as m_tools  # noqa: E402
import multi_file_analyzer as m  # noqa: E402
from matplotlib.backend_bases import MouseEvent  # noqa: E402
from test_app_integration import write_csv  # noqa: E402


class ToolsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.cleanup)
        self.path = os.path.join(self.tmp.name, "run_001.csv")
        write_csv(self.path, 1, 1)
        self.app = m.App(settings_path=os.path.join(self.tmp.name, "settings.json"))
        self.app.cache_dir = os.path.join(self.tmp.name, "cache")
        self.app.load_paths([self.path])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self.app.update()
            if not self.app._loading:
                break
            time.sleep(0.01)
        else:
            self.fail("loading did not finish")
        self.app.update()
        self.tools = self.app.tools
        self.ax = self.app.ax

    def cleanup(self):
        a = self.app
        end = time.monotonic() + 0.25      # let debounced redraw/calculate jobs run first
        while time.monotonic() < end:
            a.update()
            time.sleep(0.01)
        a._lines.clear()
        a.files.clear()
        a._result_files.clear()
        a._res_rows.clear()
        a.destroy()
        gc.collect()
        self.tmp.cleanup()

    def drag(self, x0, x1, y0=None, y1=None):
        """Synthetic mouse drag on the canvas from data coords (x0, y0) to (x1, y1)."""
        ax, cv = self.ax, self.app.canvas
        ylo, yhi = ax.get_ylim()
        y0 = ylo + (yhi - ylo) * 0.5 if y0 is None else y0
        y1 = y0 if y1 is None else y1
        p0 = ax.transData.transform((x0, y0))
        p1 = ax.transData.transform((x1, y1))
        for name, p, btn in (("button_press_event", p0, 1), ("motion_notify_event", p1, 1),
                             ("button_release_event", p1, 1)):
            cv.callbacks.process(name, MouseEvent(name, cv, p[0], p[1], button=btn))

    # (tests below)


class ChartToolsTest(ToolsBase):
    def test_default_mode_select_sets_window(self):
        self.assertEqual(self.tools.mode, "select")
        self.drag(0.005, 0.01)
        self.assertAlmostEqual(float(self.app.t0_var.get()), 0.005, delta=1e-3)
        self.assertAlmostEqual(float(self.app.t1_var.get()), 0.01, delta=1e-3)

    def test_zoom_x_sets_xlim_and_autofits_y(self):
        self.tools.zoom_x(0.01, 0.02)
        self.assertEqual(self.ax.get_xlim(), (0.01, 0.02))
        lo, hi = self.app._autoscale_y(0.01, 0.02)
        self.assertEqual(self.ax.get_ylim(), (lo, hi))
        self.assertLess(hi - lo, 40)       # fitted to the data (mean 98, sd 2.7), not the full range

    def test_zoom_y_keeps_x(self):
        xl = self.ax.get_xlim()
        self.tools.zoom_y(90, 100)
        self.assertEqual(self.ax.get_xlim(), xl)
        self.assertEqual(self.ax.get_ylim(), (90, 100))

    def test_back_restores_exact_view(self):
        xl, yl = self.ax.get_xlim(), self.ax.get_ylim()
        self.assertFalse(self.tools.can_go_back)
        self.tools.zoom_x(0.01, 0.02)
        mid = (self.ax.get_xlim(), self.ax.get_ylim())
        self.tools.zoom_y(95, 100)
        self.tools.back()
        self.assertEqual((self.ax.get_xlim(), self.ax.get_ylim()), mid)
        self.tools.back()
        self.assertEqual((self.ax.get_xlim(), self.ax.get_ylim()), (xl, yl))
        self.assertFalse(self.tools.can_go_back)
        self.tools.back()                     # no-op when empty
        self.assertEqual(self.ax.get_xlim(), xl)

    def test_history_cap_50(self):
        for i in range(60):
            self.tools.zoom_x(0.0, 0.01 + i * 1e-4)
        n = 0
        while self.tools.can_go_back:
            self.tools.back()
            n += 1
        self.assertEqual(n, 50)

    def test_reset_clears_history(self):
        self.tools.zoom_x(0.01, 0.02)
        self.tools.reset()
        self.app.update()
        self.assertFalse(self.tools.can_go_back)
        lo, hi = self.app._time_extent()
        self.assertEqual(self.ax.get_xlim(), (lo, hi))

    def test_zoom_mode_does_not_change_window(self):
        self.app.t0_var.set("0.001"); self.app.t1_var.set("0.002")
        self.tools.set_mode("zoom_x")
        self.drag(0.01, 0.02)
        self.app.update()
        self.assertEqual(self.app.t0_var.get(), "0.001")
        self.assertEqual(self.app.t1_var.get(), "0.002")
        self.assertAlmostEqual(self.ax.get_xlim()[0], 0.01, delta=1e-3)
        self.assertTrue(self.tools.can_go_back)

    def test_zoom_y_mode_drag(self):
        xl = self.ax.get_xlim()
        self.tools.set_mode("zoom_y")
        self.drag(0.01, 0.01, 90, 100)
        self.assertEqual(self.ax.get_xlim(), xl)
        self.assertAlmostEqual(self.ax.get_ylim()[0], 90, delta=0.5)
        self.assertAlmostEqual(self.ax.get_ylim()[1], 100, delta=0.5)

    def test_redraw_keeps_mode_and_history(self):
        self.tools.set_mode("zoom_x")
        self.tools.zoom_x(0.01, 0.03)
        self.app.apply_sort()
        self.app.draw()
        self.app.update()
        self.assertEqual(self.tools.mode, "zoom_x")
        self.assertTrue(self.tools.can_go_back)
        self.drag(0.015, 0.02)
        self.assertAlmostEqual(self.ax.get_xlim()[0], 0.015, delta=1e-3)

    def test_narrow_drag_ignored(self):
        self.tools.set_mode("zoom_x")
        xl = self.ax.get_xlim()
        px_per_unit = abs(self.ax.transData.transform((1, 0))[0] - self.ax.transData.transform((0, 0))[0])
        self.drag(0.01, 0.01 + 2 / px_per_unit)
        self.assertEqual(self.ax.get_xlim(), xl)
        self.assertFalse(self.tools.can_go_back)

    def test_modes_exclusive_and_keys(self):
        self.app.canvas.get_tk_widget().focus_force()
        self.app.update()
        for key, mode in (("x", "zoom_x"), ("y", "zoom_y"), ("c", "cursors"), ("w", "select")):
            self.app.canvas.get_tk_widget().event_generate(f"<KeyPress-{key}>")
            self.app.update()
            self.assertEqual(self.tools.mode, mode)
        with self.assertRaises(ValueError):
            self.tools.set_mode("pan")

    def test_back_button_disabled_when_empty(self):
        b = self.tools.back_button
        self.assertTrue(b.instate(["disabled"]))
        self.tools.zoom_x(0.01, 0.02)
        self.assertFalse(b.instate(["disabled"]))
        self.tools.back()
        self.assertTrue(b.instate(["disabled"]))
        self.tools.zoom_y(90, 100)
        self.tools.reset()
        self.app.update()
        self.assertTrue(b.instate(["disabled"]))

    def test_active_mode_button_selected(self):
        def check(mode):
            for k, b in self.tools.mode_buttons.items():
                self.assertEqual(b.instate(["selected"]), k == mode, f"{k} after {mode}")
        check("select")
        for mode in ("zoom_x", "zoom_y", "cursors", "select"):
            self.tools.mode_buttons[mode].invoke()
            self.app.update()
            check(mode)
        self.app.canvas.get_tk_widget().focus_force()
        self.app.update()
        for key, mode in (("x", "zoom_x"), ("c", "cursors"), ("y", "zoom_y")):
            self.app.canvas.get_tk_widget().event_generate(f"<KeyPress-{key}>")
            self.app.update()
            check(mode)

    def test_empty_chart_has_no_live_selector(self):
        self.tools.set_mode("zoom_x")
        self.app.set_all_shown(False)
        self.app.update()
        self.assertIsNone(self.tools._selector)
        self.drag(0.2, 0.8, 0.2, 0.2)
        self.assertFalse(self.tools.can_go_back)      # drag on the placeholder pushed nothing
        self.app.set_all_shown(True)
        self.app.update()
        self.assertFalse(self.tools.can_go_back)
        self.assertEqual(self.tools.mode, "zoom_x")
        self.assertIsNotNone(self.tools._selector)

    def test_clear_all_clears_history(self):
        self.tools.zoom_x(0.01, 0.02)
        self.app.clear_all()
        self.assertFalse(self.tools.can_go_back)


class CursorTest(ToolsBase):
    def trace(self):
        return self.app.files[0]

    def test_cursors_start_at_thirds(self):
        self.assertIsNone(self.tools.cursor_times)
        self.ax.set_xlim(0, 3)
        self.tools.set_mode("cursors")
        a, b = self.tools.cursor_times
        self.assertAlmostEqual(a, 1.0)
        self.assertAlmostEqual(b, 2.0)
        self.assertEqual(len(self.tools._cursor_lines), 2)

    def nearest_value(self, t):
        d = self.trace()["data"]
        import numpy as np
        i = int(np.argmin(np.abs(d.times_at(np.arange(d.n)) - t)))
        return float(d.values_at(np.array([i]))[0]), float(d.times_at(np.array([i]))[0])

    def test_readout_uses_nearest_sample(self):
        self.tools.set_mode("cursors")
        ta, tb = 0.0100009, 0.0171389        # between 2 us samples
        self.tools.set_cursor("A", ta)
        self.tools.set_cursor("B", tb)
        va, _ = self.nearest_value(ta)
        vb, _ = self.nearest_value(tb)
        label = self.app._label(self.trace())
        dt = tb - ta
        exp = (f"{label}   A: {ta:.6f} s, {va:.4f} mV | B: {tb:.6f} s, {vb:.4f} mV | "
               f"Δt {dt * 1e3:.3f} ms (1/Δt = {1 / dt:.1f} Hz) | ΔV {vb - va:.4f} mV")
        self.assertEqual(self.tools.readout_text(), exp)
        self.assertEqual(self.tools._readout_var.get(), exp)

    def test_dt_formatting(self):
        f = m_tools.format_dt
        self.assertEqual(f(0.007038), "Δt 7.038 ms (1/Δt = 142.1 Hz)")
        self.assertEqual(f(2.0), "Δt 2.000000 s (1/Δt = 0.5 Hz)")
        self.assertIn("(1/Δt = 20.000 kHz)", f(50e-6))
        self.assertEqual(f(0.0), "Δt 0.000 ms (1/Δt —)")

    def test_outside_range_dash(self):
        self.tools.set_mode("cursors")
        end = self.trace()["data"].t_end
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", end + 1.0)
        txt = self.tools.readout_text()
        self.assertIn(f"B: {end + 1.0:.6f} s, – mV", txt)
        self.assertTrue(txt.endswith("ΔV – mV"))
        self.assertNotIn("A: 0.010000 s, –", txt)

    def test_copy_table(self):
        write_csv(os.path.join(self.tmp.name, "run_002.csv"), 2, 2)
        self.app.load_paths([os.path.join(self.tmp.name, "run_002.csv")])
        end = time.monotonic() + 60
        while time.monotonic() < end:
            self.app.update()
            if not self.app._loading:
                break
            time.sleep(0.01)
        self.app.update()
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        lines = self.tools.cursor_table().split("\n")
        self.assertEqual(lines[0], "Trace\tt_A (s)\tV_A (mV)\tt_B (s)\tV_B (mV)\tΔV (mV)\tΔt (s)\t1/Δt (Hz)"
                                   "\tY1 (mV)\tY2 (mV)\tΔY (mV)")
        shown = [f for f in self.app.files if f["show"]]
        self.assertEqual(len(shown), 2)
        self.assertEqual(len(lines), 3)
        for line, f in zip(lines[1:], shown):
            cells = line.split("\t")
            self.assertEqual(len(cells), 11)
            self.assertEqual(cells[0], self.app._label(f))
            self.assertEqual(cells[1], "0.010000")
            self.assertEqual(cells[6], "0.010000")
            self.assertEqual(cells[7], "100.0")
        self.tools.copy_cursor_values()
        self.assertEqual(self.app.clipboard_get(), "\n".join(lines))

    def test_cursors_survive_redraw(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        self.app.draw()
        self.app.update()
        self.assertEqual(self.tools.cursor_times, (0.01, 0.02))
        self.assertEqual(len(self.tools._cursor_lines), 2)
        for ln in self.tools._cursor_lines:
            self.assertIn(ln, self.ax.lines)
        self.assertIn("A: 0.010000 s", self.tools._readout_var.get())

    def cursor_lines_on_axes(self):
        cols = {m_tools.CURSOR_COLORS["A"], m_tools.CURSOR_COLORS["B"]}     # vertical cursors only
        return [ln for ln in self.ax.lines if ln.get_color() in cols]

    def test_leaving_cursors_hides_lines_keeps_times(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        self.assertEqual(len(self.cursor_lines_on_axes()), 2)
        self.tools.set_mode("select")
        self.assertEqual(self.cursor_lines_on_axes(), [])
        self.assertEqual(self.tools.cursor_times, (0.01, 0.02))
        self.tools.set_mode("cursors")
        self.assertEqual(self.tools.cursor_times, (0.01, 0.02))
        self.assertEqual(len(self.cursor_lines_on_axes()), 2)
        self.tools.set_mode("cursors")
        self.tools.set_mode("cursors")
        self.assertEqual(len(self.cursor_lines_on_axes()), 2)
        self.tools.set_cursor("A", 0.015)
        xs = sorted(ln.get_xdata()[0] for ln in self.cursor_lines_on_axes())
        self.assertEqual(xs, [0.015, 0.02])

    def test_drag_blits_and_many_traces_stay_fast(self):
        base = self.app.files[0]
        for i in range(159):
            f = dict(base); f["name"] = f"x{i}"
            self.app.files.append(f)
        self.app.draw(); self.app.update()
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        cv = self.app.canvas
        leg_box = self.app._legend.get_window_extent()
        y = self.ax.transData.transform((0, 98))[1]
        x0 = self.ax.transData.transform((0.01, 0))[0]
        self.app.update()
        tp = time.perf_counter()
        cv.callbacks.process("button_press_event", MouseEvent("button_press_event", cv, x0, y, button=1))
        print(f"[160 traces] press {(time.perf_counter() - tp) * 1000:.0f} ms", end=" ")
        self.assertIsNotNone(self.tools._bg)
        t0 = time.perf_counter()
        for k in range(30):
            cv.callbacks.process("motion_notify_event",
                                 MouseEvent("motion_notify_event", cv, x0 + 3 * k, y, button=1))
        per = (time.perf_counter() - t0) / 30
        cv.callbacks.process("button_release_event", MouseEvent("button_release_event", cv, x0 + 90, y, button=1))
        print(f"[160 traces] motion handling {per * 1000:.1f} ms/event", end=" ")
        self.assertGreater(self.tools.cursor_times[0], 0.01)
        self.assertLess(per, 0.1)
        self.assertEqual(leg_box.bounds, self.app._legend.get_window_extent().bounds)   # legend did not move
        del self.app.files[1:]

    def _cursor_pixel_hits(self, t):
        """Rendered pixels near the top edge of the axes at time t that match cursor A's colour."""
        import numpy as np
        from matplotlib.colors import to_rgb
        cv = self.app.canvas
        buf = np.asarray(cv.buffer_rgba())
        x, y = self.ax.transData.transform((t, 0))[0], self.ax.bbox.y1 - 3
        row = buf[int(round(buf.shape[0] - y)), int(round(x)) - 2:int(round(x)) + 3, :3]
        want = np.array(to_rgb(m_tools.CURSOR_COLORS["A"])) * 255
        return int((np.abs(row.astype(float) - want).sum(axis=1) < 60).sum())

    def test_cursor_lines_animated_and_visible_after_draw(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.app.canvas.draw()
        self.assertTrue(all(ln.get_animated() for ln in self.tools._cursor_lines))
        self.assertIsNotNone(self.tools._bg)
        self.assertGreater(self._cursor_pixel_hits(0.01), 0)
        self.ax.set_xlim(0, 0.05)                      # zoom: lines still blitted after the render
        self.app.canvas.draw()
        self.assertGreater(self._cursor_pixel_hits(0.01), 0)

    def test_grab_does_not_render(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.app.update()
        cv = self.app.canvas
        y = self.ax.transData.transform((0, 98))[1]
        x0 = self.ax.transData.transform((0.01, 0))[0]
        with mock.patch.object(cv, "draw") as draw:
            cv.callbacks.process("button_press_event", MouseEvent("button_press_event", cv, x0, y, button=1))
            self.assertEqual(self.tools._dragging, "A")
            cv.callbacks.process("motion_notify_event", MouseEvent("motion_notify_event", cv, x0 + 20, y, button=1))
            cv.callbacks.process("button_release_event", MouseEvent("button_release_event", cv, x0 + 20, y, button=1))
        draw.assert_not_called()
        self.assertGreater(self.tools.cursor_times[0], 0.01)
        self.assertTrue(all(ln.get_animated() for ln in self.tools._cursor_lines))

    def test_copy_button_label_and_state(self):
        b = self.tools.copy_button
        self.assertEqual(b.cget("text"), "Copy cursor values")
        self.assertIn("disabled", b.state())
        self.tools.set_mode("cursors")
        self.assertNotIn("disabled", b.state())
        self.tools.set_mode("select")
        self.assertIn("disabled", b.state())
        self.assertEqual(self.tools._readout_var.get(), "")

    def test_legend_drag_only_outside_cursors_mode(self):
        leg = self.app._legend
        self.assertIsNotNone(leg)
        self.assertTrue(leg.get_draggable())
        self.tools.set_mode("cursors")
        self.assertFalse(self.app._legend.get_draggable())
        self.app.draw()
        self.assertFalse(self.app._legend.get_draggable())
        self.tools.set_mode("select")
        self.assertTrue(self.app._legend.get_draggable())

    def test_drag_grabs_nearest_line_and_no_history(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        px = self.ax.transData.transform((0.01, 0))[0] + 4        # within 6 px
        py = self.ax.transData.transform((0.01, 98))[1]
        cv = self.app.canvas
        tx = 0.015
        qx = self.ax.transData.transform((tx, 0))[0]
        for name, x in (("button_press_event", px), ("motion_notify_event", qx),
                        ("button_release_event", qx)):
            cv.callbacks.process(name, MouseEvent(name, cv, x, py, button=1))
        a, b = self.tools.cursor_times
        self.assertAlmostEqual(a, tx, delta=1e-5)
        self.assertEqual(b, 0.02)
        self.assertFalse(self.tools.can_go_back)
        # far from any line: nothing moves
        far = self.ax.transData.transform((0.03, 0))[0]
        for name, x in (("button_press_event", far), ("motion_notify_event", far + 50),
                        ("button_release_event", far + 50)):
            cv.callbacks.process(name, MouseEvent(name, cv, x, py, button=1))
        self.assertEqual(self.tools.cursor_times[1], 0.02)

    def test_readout_follows_selection_and_empty(self):
        self.tools.set_mode("cursors")
        self.assertNotEqual(self.tools._readout_var.get(), "")
        self.app.set_all_shown(False)
        self.app.update()
        self.assertEqual(self.tools._readout_var.get(), "")
        self.assertEqual(self.tools.cursor_table(), "")


class LevelCursorTest(ToolsBase):
    def test_default_levels_on_first_cursors_mode(self):
        self.assertIsNone(self.tools.cursor_levels)
        lo, hi = sorted(self.ax.get_ylim())
        self.tools.set_mode("cursors")
        y1, y2 = self.tools.cursor_levels
        self.assertAlmostEqual(y1, lo + (hi - lo) / 3)
        self.assertAlmostEqual(y2, lo + 2 * (hi - lo) / 3)
        self.assertEqual(len(self.tools._level_lines), 2)
        for ln, y in zip(self.tools._level_lines, (y1, y2)):
            self.assertTrue(ln.get_animated())
            self.assertEqual(list(ln.get_ydata()), [y, y])

    def test_level_text_and_readout(self):
        self.tools.set_mode("cursors")
        self.tools.set_level("Y1", 10.0)
        self.tools.set_level("Y2", 12.5)
        self.assertEqual(self.tools.level_text(),
                         "Y1: 10.0000 mV | Y2: 12.5000 mV | ΔY (Y2−Y1) 2.5000 mV")
        self.assertEqual(self.tools._readout_var.get(), self.tools.readout_text())
        self.assertEqual(self.tools._level_var.get(), self.tools.level_text())
        with self.assertRaises(ValueError):
            self.tools.set_level("Y3", 1.0)

    def test_drag_nearest_horizontal_line(self):
        self.tools.set_mode("cursors")
        self.app.update()
        x0, x1 = self.ax.get_xlim()
        self.tools.set_cursor("A", x0 + 0.1 * (x1 - x0))
        self.tools.set_cursor("B", x0 + 0.9 * (x1 - x0))
        times = self.tools.cursor_times
        y1, y2 = self.tools.cursor_levels
        px, py = self.ax.transData.transform((x0 + 0.5 * (x1 - x0), y1))
        cv = self.app.canvas
        with mock.patch.object(cv, "draw") as draw:
            cv.callbacks.process("button_press_event",
                                 MouseEvent("button_press_event", cv, px, py + 2, button=1))
            self.assertEqual(self.tools._dragging, "Y1")
            for name in ("motion_notify_event", "button_release_event"):
                cv.callbacks.process(name, MouseEvent(name, cv, px, py + 20, button=1))
        draw.assert_not_called()
        self.assertGreater(self.tools.cursor_levels[0], y1)
        self.assertEqual(self.tools.cursor_levels[1], y2)
        self.assertEqual(self.tools.cursor_times, times)

    def test_levels_survive_zoom_and_back(self):
        self.tools.set_mode("cursors")
        self.tools.set_level("Y1", 97.0)
        self.tools.set_level("Y2", 99.0)
        x0, x1 = self.ax.get_xlim()
        self.tools.zoom_x(x0, x0 + (x1 - x0) / 4)
        self.tools.back()
        self.tools.reset()
        self.app.update()
        self.assertEqual(self.tools.cursor_levels, (97.0, 99.0))

    def test_levels_survive_redraw(self):
        self.tools.set_mode("cursors")
        self.tools.set_level("Y1", 97.0)
        self.tools.set_level("Y2", 99.0)
        self.app.draw(reset_view=True)
        self.app.update()
        self.assertEqual(self.tools.cursor_levels, (97.0, 99.0))
        self.assertEqual([list(ln.get_ydata()) for ln in self.tools._level_lines],
                         [[97.0, 97.0], [99.0, 99.0]])
        for ln in self.tools._level_lines:
            self.assertIs(ln.axes, self.ax)

    def test_levels_hidden_outside_cursors_mode(self):
        self.tools.set_mode("cursors")
        self.tools.set_level("Y1", 97.0)
        self.tools.set_mode("select")
        self.assertEqual(self.tools._level_lines, [])
        self.assertEqual(self.tools._readout_var.get(), "")
        self.assertEqual(self.tools._level_var.get(), "")
        self.assertEqual(self.tools.cursor_levels[0], 97.0)
        self.tools.set_mode("cursors")
        self.assertEqual(self.tools.cursor_levels[0], 97.0)
        self.assertEqual(len(self.tools._level_lines), 2)

    def test_offscreen_level_markers(self):
        self.tools.set_mode("cursors")
        lo, hi = sorted(self.ax.get_ylim())
        self.tools.set_level("Y2", hi + 10)
        self.tools.set_level("Y1", lo - 10)
        vis = {t.get_text() for t in self.tools._level_markers if t.get_visible()}
        self.assertEqual(vis, {"Y2 ▲", "Y1 ▼"})
        self.tools.set_level("Y2", (lo + hi) / 2)
        vis = {t.get_text() for t in self.tools._level_markers if t.get_visible()}
        self.assertEqual(vis, {"Y1 ▼"})

    def test_level_and_time_markers_do_not_overlap(self):
        self.tools.set_mode("cursors")
        x0, x1 = self.ax.get_xlim()
        lo, hi = sorted(self.ax.get_ylim())
        self.tools.set_cursor("B", x1 + (x1 - x0))          # B off to the right
        self.tools.set_level("Y2", hi + (hi - lo))          # Y2 off the top
        self.app.canvas.draw()
        r = self.app.canvas.get_renderer()
        b = [t for t in self.tools._markers if t.get_visible()]
        y = [t for t in self.tools._level_markers if t.get_visible()]
        self.assertEqual([t.get_text() for t in b], ["B \u25b6"])
        self.assertEqual([t.get_text() for t in y], ["Y2 \u25b2"])
        self.assertFalse(b[0].get_window_extent(r).overlaps(y[0].get_window_extent(r)))

    def test_save_image_includes_level_cursors(self):
        self.tools.set_mode("cursors")
        self.tools.set_level("Y1", 97.0)
        states = []
        fig = self.ax.figure
        with mock.patch.object(m_tools.filedialog, "asksaveasfilename", return_value="x.png"), \
                mock.patch.object(fig, "savefig",
                                  side_effect=lambda *a, **k: states.extend(
                                      ln.get_animated() for ln in self.tools._cursor_lines + self.tools._level_lines)):
            self.tools.save_image()
        self.assertEqual(states, [False] * 4)                # A, B, Y1, Y2 all drawn into the image
        self.assertTrue(all(ln.get_animated() for ln in self.tools._level_lines))

    def test_cursors_to_view_moves_levels(self):
        self.tools.set_mode("cursors")
        self.tools.zoom_y(90.0, 120.0)
        self.tools.cursors_to_view()
        y1, y2 = self.tools.cursor_levels
        self.assertAlmostEqual(y1, 100.0)
        self.assertAlmostEqual(y2, 110.0)

    def test_copy_table_has_levels(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.01)
        self.tools.set_cursor("B", 0.02)
        self.tools.set_level("Y1", 97.5)
        self.tools.set_level("Y2", 99.25)
        lines = self.tools.cursor_table().split("\n")
        self.assertTrue(lines[0].endswith("\tY1 (mV)\tY2 (mV)\tΔY (mV)"))
        for line in lines[1:]:
            self.assertEqual(line.split("\t")[-3:], ["97.5000", "99.2500", "1.7500"])


if __name__ == "__main__":
    unittest.main()
