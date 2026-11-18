"""Typed axis ranges on the chart axes, Cursors to view, off-screen markers, typed cursor times."""
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import chart_tools as m_tools  # noqa: E402
from matplotlib.backend_bases import MouseEvent, ResizeEvent  # noqa: E402
from test_chart_tools import ToolsBase  # noqa: E402

BAD = "#fecaca"
LEFT, RIGHT = "◀ ", " ▶"


class AxisEntryBase(ToolsBase):
    def press(self, x, y, dbl=False, button=1):
        cv = self.app.canvas
        cv.callbacks.process("button_press_event",
                             MouseEvent("button_press_event", cv, x, y, button=button, dblclick=dbl))

    def x_axis_point(self):
        b = self.ax.bbox
        return (b.x0 + b.x1) / 2, b.y0 - 12

    def y_axis_point(self):
        b = self.ax.bbox
        return b.x0 - 12, (b.y0 + b.y1) / 2

    def entries(self):
        e = self.tools._axis_entries
        return None if e is None else (e["lo"], e["hi"])

    def type_in(self, entry, text, key="Return"):
        entry.delete(0, "end")
        entry.insert(0, text)
        entry.focus_force()
        self.app.update()
        entry.event_generate(f"<{key}>")
        self.app.update()

    def is_red(self, entry):
        return str(entry.cget("bg")).lower() == BAD


class AxisEntryTest(AxisEntryBase):
    def test_dblclick_x_axis_shows_prefilled_entries(self):
        self.assertIsNone(self.entries())
        self.press(*self.x_axis_point(), dbl=True)
        lo, hi = self.entries()
        xl = self.ax.get_xlim()
        self.assertEqual(lo.get(), f"{xl[0]:.6g}")
        self.assertEqual(hi.get(), f"{xl[1]:.6g}")
        self.assertTrue(lo.winfo_manager() == "place" and hi.winfo_manager() == "place")
        self.assertLess(float(lo.place_info()["x"]), float(hi.place_info()["x"]))

    def test_dblclick_y_axis_prefilled_and_bottom_below_top(self):
        self.press(*self.y_axis_point(), dbl=True)
        lo, hi = self.entries()
        yl = self.ax.get_ylim()
        self.assertEqual((lo.get(), hi.get()), (f"{yl[0]:.6g}", f"{yl[1]:.6g}"))
        self.assertGreater(float(lo.place_info()["y"]), float(hi.place_info()["y"]))   # Tk y is top-down
        self.assertAlmostEqual(float(lo.place_info()["x"]), self.ax.bbox.x0 - 2, delta=0.5)
        self.assertEqual(lo.place_info()["anchor"], "se")     # right edge left of the plot, never over it

    def test_resize_closes_entries(self):
        self.press(*self.x_axis_point(), dbl=True)
        self.app.geometry("1000x700")
        for _ in range(20):
            self.app.update()
            time.sleep(0.01)
        self.assertIsNone(self.entries())
        self.press(*self.y_axis_point(), dbl=True)
        cv = self.app.canvas
        cv.callbacks.process("resize_event", ResizeEvent("resize_event", cv))
        self.assertIsNone(self.entries())

    def test_single_click_and_plot_area_dblclick_do_nothing(self):
        self.press(*self.x_axis_point())
        self.assertIsNone(self.entries())
        b = self.ax.bbox
        self.press((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, dbl=True)
        self.assertIsNone(self.entries())

    def test_enter_applies_x_pushes_history_and_keeps_y(self):
        xl, yl = self.ax.get_xlim(), self.ax.get_ylim()
        self.press(*self.x_axis_point(), dbl=True)
        lo, hi = self.entries()
        lo.delete(0, "end"); lo.insert(0, "0.01")
        self.type_in(hi, "0.02")
        self.assertEqual(self.ax.get_xlim(), (0.01, 0.02))
        self.assertEqual(self.ax.get_ylim(), yl)
        self.assertIsNone(self.entries())
        self.assertTrue(self.tools.can_go_back)
        self.tools.back()
        self.assertEqual((self.ax.get_xlim(), self.ax.get_ylim()), (xl, yl))

    def test_enter_applies_y(self):
        xl = self.ax.get_xlim()
        self.press(*self.y_axis_point(), dbl=True)
        lo, hi = self.entries()
        lo.delete(0, "end"); lo.insert(0, "90")
        self.type_in(hi, "100")
        self.assertEqual(self.ax.get_ylim(), (90.0, 100.0))
        self.assertEqual(self.ax.get_xlim(), xl)
        self.assertTrue(self.tools.can_go_back)
        self.tools.back()
        self.assertNotEqual(self.ax.get_ylim(), (90.0, 100.0))

    def test_invalid_marks_red_and_changes_nothing(self):
        for text in ("abc", "nan", "inf", ""):
            with self.subTest(text=text):
                xl = self.ax.get_xlim()
                self.press(*self.x_axis_point(), dbl=True)
                lo, hi = self.entries()
                self.type_in(hi, text)
                self.assertTrue(self.is_red(hi))
                self.assertFalse(self.is_red(lo))
                self.assertIsNotNone(self.entries())
                self.assertEqual(self.ax.get_xlim(), xl)
                self.assertFalse(self.tools.can_go_back)
                self.tools.close_axis_entries()

    def test_low_ge_high_is_invalid(self):
        xl = self.ax.get_xlim()
        self.press(*self.y_axis_point(), dbl=True)
        lo, hi = self.entries()
        lo.delete(0, "end"); lo.insert(0, "100")
        self.type_in(hi, "90")
        self.assertTrue(self.is_red(lo) or self.is_red(hi))
        self.assertIsNotNone(self.entries())
        self.assertFalse(self.tools.can_go_back)
        self.assertEqual(self.ax.get_xlim(), xl)

    def test_escape_cancels(self):
        yl = self.ax.get_ylim()
        self.press(*self.y_axis_point(), dbl=True)
        lo, hi = self.entries()
        self.type_in(lo, "50", key="Escape")
        self.assertIsNone(self.entries())
        self.assertFalse(lo.winfo_exists())
        self.assertEqual(self.ax.get_ylim(), yl)
        self.assertFalse(self.tools.can_go_back)

    def test_click_elsewhere_and_focus_loss_cancel(self):
        self.press(*self.x_axis_point(), dbl=True)
        self.press(*self.x_axis_point())            # plain click
        self.assertIsNone(self.entries())
        self.press(*self.x_axis_point(), dbl=True)
        self.tools._on_entry_focus_out()            # focus moved away from both boxes
        self.app.update()
        self.assertIsNone(self.entries())

    def test_draw_and_reset_remove_entries(self):
        self.press(*self.x_axis_point(), dbl=True)
        self.app.draw()
        self.assertIsNone(self.entries())
        self.press(*self.x_axis_point(), dbl=True)
        self.tools.reset()
        self.app.update()
        self.assertIsNone(self.entries())
        self.press(*self.x_axis_point(), dbl=True)
        self.tools.detach()
        self.assertIsNone(self.entries())

    def test_works_in_every_mode_without_drag_action(self):
        for mode in ("select", "zoom_x", "zoom_y", "cursors"):
            with self.subTest(mode=mode):
                self.tools.set_mode(mode)
                self.app.update()
                t0, t1 = self.app.t0_var.get(), self.app.t1_var.get()
                xl, cur = self.ax.get_xlim(), self.tools.cursor_times
                self.press(*self.x_axis_point(), dbl=True)
                self.assertIsNotNone(self.entries())
                self.assertEqual((self.app.t0_var.get(), self.app.t1_var.get()), (t0, t1))
                self.assertEqual(self.ax.get_xlim(), xl)
                self.assertEqual(self.tools.cursor_times, cur)
                self.assertFalse(self.tools.can_go_back)
                self.tools.close_axis_entries()

    def test_typed_x_in_cursors_mode_keeps_cursors(self):
        self.tools.set_mode("cursors")
        cur = self.tools.cursor_times
        self.press(*self.x_axis_point(), dbl=True)
        lo, hi = self.entries()
        lo.delete(0, "end"); lo.insert(0, "0.01")
        self.type_in(hi, "0.02")
        self.assertEqual(self.tools.cursor_times, cur)
        self.assertEqual(self.ax.get_xlim(), (0.01, 0.02))


class CursorFeatureTest(AxisEntryBase):
    def shown(self):
        return {t.get_text() for t in self.tools._markers if t.get_visible()}

    def test_cursors_to_view_button_and_key(self):
        b = self.tools.view_button
        self.assertEqual(b.cget("text"), "Cursors to view")
        self.assertIn("disabled", b.state())
        self.tools.set_mode("cursors")
        self.assertNotIn("disabled", b.state())
        self.tools.zoom_x(0.01, 0.04)
        b.invoke()
        ta, tb = self.tools.cursor_times
        self.assertAlmostEqual(ta, 0.02, places=9)
        self.assertAlmostEqual(tb, 0.03, places=9)
        self.tools.zoom_x(0.1, 0.4)
        n = len(self.tools._history)
        w = self.app.canvas.get_tk_widget()
        w.focus_force()
        self.app.update()
        w.event_generate("<KeyPress-r>")
        self.app.update()
        ta, tb = self.tools.cursor_times
        self.assertAlmostEqual(ta, 0.2, places=9)
        self.assertAlmostEqual(tb, 0.3, places=9)
        self.assertEqual(len(self.tools._history), n)      # moving cursors adds no history

    def test_key_r_ignored_outside_cursors_mode(self):
        self.tools.cursor_times = (0.001, 0.002)
        self.tools.zoom_x(0.01, 0.04)
        self.app.canvas.get_tk_widget().focus_force()
        self.app.update()
        self.app.canvas.get_tk_widget().event_generate("<KeyPress-r>")
        self.app.update()
        self.assertEqual(self.tools.cursor_times, (0.001, 0.002))

    def test_offscreen_markers_follow_view(self):
        self.tools.set_mode("cursors")
        self.tools.set_cursor("A", 0.001)
        self.tools.set_cursor("B", 0.03)
        self.app.canvas.draw()
        self.assertEqual(self.shown(), set())
        self.tools.zoom_x(0.01, 0.02)
        self.app.canvas.draw()
        self.assertEqual(self.shown(), {LEFT + "A", "B" + RIGHT})
        for t in self.tools._markers:
            if t.get_visible():
                which = t.get_text().replace("◀", "").replace("▶", "").strip()
                self.assertEqual(t.get_color(), m_tools.CURSOR_COLORS[which])
        self.assertIn("B:", self.tools._readout_var.get())       # readout still has values
        self.tools.back()
        self.app.canvas.draw()
        self.assertEqual(self.shown(), set())

    def test_both_off_same_side_stack_and_move_with_cursor(self):
        self.tools.set_mode("cursors")
        self.tools.zoom_x(0.02, 0.03)
        self.tools.set_cursor("A", 0.001)
        self.tools.set_cursor("B", 0.002)
        self.app.canvas.draw()
        vis = [t for t in self.tools._markers if t.get_visible()]
        self.assertEqual(len(vis), 2)
        self.assertNotEqual(vis[0].get_position()[1], vis[1].get_position()[1])
        self.tools.set_cursor("A", 0.025)
        self.assertEqual(self.shown(), {LEFT + "B"})

    def test_markers_only_in_cursors_mode(self):
        self.assertEqual(self.tools._markers, [])
        self.tools.set_mode("cursors")
        self.tools.set_mode("select")
        self.assertEqual(self.tools._markers, [])

    def test_typed_cursor_time_moves_line_without_history(self):
        self.tools.set_mode("cursors")
        ea, eb = self.tools.cursor_entries["A"], self.tools.cursor_entries["B"]
        ta, tb = self.tools.cursor_times
        self.assertEqual(ea.get(), f"{ta:.6f}")
        self.assertEqual(eb.get(), f"{tb:.6f}")
        self.type_in(ea, "0.012")
        self.assertEqual(self.tools.cursor_times[0], 0.012)
        self.assertEqual(list(self.tools._cursor_lines[0].get_xdata()), [0.012, 0.012])
        self.assertIn("0.012000", self.tools._readout_var.get())
        self.assertEqual(ea.get(), "0.012000")
        self.assertFalse(self.tools.can_go_back)
        self.assertEqual(self.tools.cursor_times[1], tb)

    def test_typed_cursor_time_invalid_is_red(self):
        self.tools.set_mode("cursors")
        eb = self.tools.cursor_entries["B"]
        cur = self.tools.cursor_times
        self.type_in(eb, "xyz")
        self.assertTrue(self.is_red(eb))
        self.assertEqual(self.tools.cursor_times, cur)
        self.type_in(eb, "inf")
        self.assertTrue(self.is_red(eb))
        self.assertEqual(self.tools.cursor_times, cur)

    def test_entries_follow_dragged_cursor_and_disable_outside_mode(self):
        for e in self.tools.cursor_entries.values():
            self.assertEqual(str(e.cget("state")), "disabled")
        self.tools.set_mode("cursors")
        for e in self.tools.cursor_entries.values():
            self.assertEqual(str(e.cget("state")), "normal")
        self.tools.set_cursor("B", 0.0345, _blit=True)
        self.assertEqual(self.tools.cursor_entries["B"].get(), "0.034500")
        self.tools.set_mode("select")
        for e in self.tools.cursor_entries.values():
            self.assertEqual(str(e.cget("state")), "disabled")

    def test_cursor_controls_are_visible_in_cursors_mode(self):
        self.tools.set_mode("cursors")
        for _ in range(5):
            self.app.update()
        for w in (*self.tools.cursor_entries.values(), self.tools.view_button, self.tools.copy_button):
            self.assertTrue(w.winfo_ismapped())      # not squeezed out by the long readout text

    def test_focused_cursor_entry_not_overwritten_by_refresh(self):
        self.tools.set_mode("cursors")
        ea = self.tools.cursor_entries["A"]
        ea.focus_force()
        self.app.update()
        ea.delete(0, "end"); ea.insert(0, "0.0")
        self.tools.set_cursor("B", 0.03)          # triggers refresh_readout
        self.assertEqual(ea.get(), "0.0")
        self.assertEqual(self.tools.cursor_entries["B"].get(), "0.030000")
        self.type_in(ea, "0.0123")                # Enter applies and reformats
        self.assertEqual(ea.get(), "0.012300")

    def test_markers_do_not_force_renders_on_grab(self):
        self.tools.set_mode("cursors")
        self.tools.zoom_x(0.01, 0.02)
        self.tools.set_cursor("A", 0.015)
        self.tools.set_cursor("B", 0.03)
        self.app.canvas.draw()
        cv = self.app.canvas
        y = self.ax.transData.transform((0, 98))[1]
        x0 = self.ax.transData.transform((0.015, 0))[0]
        with mock.patch.object(cv, "draw") as draw:
            cv.callbacks.process("button_press_event", MouseEvent("button_press_event", cv, x0, y, button=1))
            cv.callbacks.process("motion_notify_event", MouseEvent("motion_notify_event", cv, x0 - 480, y, button=1))
            cv.callbacks.process("button_release_event", MouseEvent("button_release_event", cv, x0 - 480, y, button=1))
        draw.assert_not_called()
        self.assertIn(LEFT + "A", self.shown())


if __name__ == "__main__":
    unittest.main()
