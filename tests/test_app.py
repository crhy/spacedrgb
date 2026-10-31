import unittest
from unittest.mock import patch, Mock
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
from spaced_rgb.app import Window
from spaced_rgb.core import rgb


@unittest.skipUnless(Gtk.init_check()[0], "Needs a display (use xvfb-run)")
class Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application()
        cls.application.register(None)

    def setUp(self):
        with patch.object(Window, "scan"), patch.object(Window, "refresh_presets"):
            self.window = Window(self.application)
        for key in ("ram", "gpu", "cooler"):
            check = Gtk.CheckButton()
            effects = Gtk.ComboBoxText()
            effects.append("Static", "Static")
            effects.append("Effect: Rainbow", "Rainbow")
            effects.set_active_id("Effect: Rainbow")
            check.connect("toggled", self.window.sync_selection)
            self.window.rows[key] = check, effects
        self.window.sync_selection()

    def tearDown(self):
        self.window.on_close()
        self.window.pool.shutdown(wait=True)
        self.window.destroy()

    def test_select_all_mixed_and_empty(self):
        window = self.window
        window.select_all.set_active(True)
        self.assertEqual(len(window.selection()), 3)
        window.rows["ram"][0].set_active(False)
        self.assertTrue(window.select_all.get_inconsistent())
        window.select_all.set_active(True)
        self.assertFalse(window.select_all.get_inconsistent())
        self.assertEqual(len(window.selection()), 3)
        window.select_all.set_active(False)
        self.assertEqual(window.selection(), {})
        window.rows = {}
        window.sync_selection()
        self.assertFalse(window.select_all.get_sensitive())

    def test_green_swatch_selects_static_and_exact_rgb(self):
        window = self.window
        window.select_all.set_active(True)
        window.solid_color("#00ff00")
        self.assertEqual(window.color_hex(), "#00ff00")
        self.assertEqual(rgb(window.hex_entry.get_text(), 100), (0, 255, 0))
        self.assertEqual(set(window.selection().values()), {"Static"})

    def test_typed_hex_used_for_preset(self):
        window = self.window
        window.hex_entry.set_text("#00ff00")
        window.select_all.set_active(True)
        window.preset.get_child().set_text("green")
        with patch.object(window.presets, "save") as save, patch.object(window, "refresh_presets"):
            window.save_preset(None)
        self.assertEqual(save.call_args.args[1], "#00ff00")

    def test_cpu_display_mode_starts_without_image(self):
        window = self.window
        window.lcd_mode.set_active_id("cpu")
        with patch.object(window, "lcd_tick"):
            window.start_lcd(None)
        self.assertEqual(window.lcd_settings, ("cpu", None, 0))
        self.assertTrue(window.lcd_active)

    def test_hardware_enable_opens_authentication_without_extra_dialog(self):
        window = self.window
        self.assertEqual(window.setup_button.get_label(), "Hardware Enable")
        with patch("spaced_rgb.app.Gtk.MessageDialog") as dialog, \
                patch("spaced_rgb.app.subprocess.run", return_value=Mock(returncode=0, stdout="ok")) as run, \
                patch.object(window.engine, "refresh_hardware") as refresh, \
                patch.object(window, "scan") as scan, \
                patch.object(window, "work", side_effect=lambda function, done: done(function())):
            window.hardware_setup(None)
        dialog.assert_not_called()
        self.assertEqual(run.call_args.args[0][0], "pkexec")
        refresh.assert_called_once_with()
        scan.assert_called_once_with()

    def test_flatpak_enable_authenticates_on_host_with_bundled_script(self):
        window = self.window
        with patch.dict("os.environ", {"FLATPAK_ID": "io.github.crhy.SpacedRGB"}), \
                patch("spaced_rgb.app.subprocess.run", return_value=Mock(returncode=0, stdout="ok")) as run, \
                patch.object(window.engine, "refresh_hardware"), \
                patch.object(window, "scan"), \
                patch.object(window, "work", side_effect=lambda function, done: done(function())):
            window.hardware_setup(None)
        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["flatpak-spawn", "--host", "pkexec", "bash", "-c"])
        self.assertIn("udevadm", command[5])
        self.assertEqual(command[6], "spaced-rgb-hardware-enable")
        self.assertTrue(command[7].isdigit())

    def test_cancelled_enable_does_not_restart_backend_or_rescan(self):
        window = self.window
        with patch("spaced_rgb.app.subprocess.run", return_value=Mock(returncode=1, stderr="cancelled")), \
                patch.object(window.engine, "refresh_hardware") as refresh, \
                patch.object(window, "scan") as scan, \
                patch.object(window, "work", side_effect=lambda function, done: done(function())):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                window.hardware_setup(None)
        refresh.assert_not_called()
        scan.assert_not_called()

    def test_apply_during_background_update_is_queued_and_runs(self):
        window = self.window
        gate = threading.Event()
        window.select_all.set_active(True)
        try:
            with patch.object(window.engine, "apply", return_value=[]) as apply:
                window.work(lambda: gate.wait(3), lambda _: None, foreground=False)
                window.apply()
                self.assertEqual(len(window.pending), 1)
                apply.assert_not_called()
                gate.set()
                deadline = time.monotonic() + 3
                while (window.busy or window.pending) and time.monotonic() < deadline:
                    GLib.MainContext.default().iteration(False)
                    time.sleep(0.005)
                apply.assert_called_once()
        finally:
            gate.set()
