# SPDX-License-Identifier: GPL-3.0-or-later
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import logging
import os
import subprocess

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from .core import Engine, Presets, ROOT, rgb


logger = logging.getLogger(__name__)


class Window(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="Spaced RGB")
        self.set_default_size(820, 620)
        self.engine = Engine()
        self.presets = Presets()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.rows = {}
        self.busy = False
        self.pending = deque()
        self.closing = False
        self.selecting = False
        self.lcd_timer = None
        self.lcd_active = False
        self.connect("delete-event", self.on_close)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=20)
        self.add(outer)
        header = Gtk.Box(spacing=10)
        title = Gtk.Label(xalign=0, hexpand=True)
        title.set_markup("<span size='xx-large' weight='bold'>Spaced RGB</span>\nRAM · Graphics · Cooler")
        header.pack_start(title, True, True, 0)
        self.scan_button = Gtk.Button(label="Detect devices")
        self.scan_button.connect("clicked", lambda _: self.scan())
        header.add(self.scan_button)
        self.setup_button = Gtk.Button(label="Hardware Enable")
        self.setup_button.connect("clicked", self.hardware_setup)
        header.add(self.setup_button)
        outer.add(header)
        self.status = Gtk.Label(label="Detecting lighting controllers…", xalign=0, wrap=True)
        self.status.set_selectable(True)
        outer.add(self.status)
        self.select_all = Gtk.CheckButton(label="Select all")
        self.select_all.set_sensitive(False)
        self.select_all.connect("toggled", self.toggle_all)
        outer.add(self.select_all)
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller.add(self.devices)
        outer.pack_start(scroller, True, True, 0)
        controls = Gtk.Box(spacing=12)
        controls.add(Gtk.Label(label="Color"))
        self.color = Gtk.ColorButton(title="Lighting color", use_alpha=False)
        rgba = Gdk.RGBA()
        rgba.parse("#4588ff")
        self.color.set_rgba(rgba)
        controls.add(self.color)
        self.hex_entry = Gtk.Entry(text="#4588ff", width_chars=8, max_length=7)
        self.hex_entry.set_tooltip_text("Exact RGB color, for example #00ff00 for pure green")
        self.color.connect("notify::rgba", lambda *_: self.hex_entry.set_text(self.color_hex()))
        controls.add(self.hex_entry)
        controls.add(Gtk.Label(label="Brightness"))
        self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.brightness.set_value(70)
        self.brightness.set_digits(0)
        self.brightness.set_hexpand(True)
        controls.pack_start(self.brightness, True, True, 0)
        self.apply_button = Gtk.Button(label="Apply selected")
        self.apply_button.get_style_context().add_class("suggested-action")
        self.apply_button.connect("clicked", lambda _: self.apply())
        controls.add(self.apply_button)
        self.off_button = Gtk.Button(label="Lights off")
        self.off_button.connect("clicked", lambda _: self.apply(off=True))
        controls.add(self.off_button)
        outer.add(controls)
        colors = Gtk.Box(spacing=8)
        colors.add(Gtk.Label(label="Solid color"))
        for label, value in (("Red", "#ff0000"), ("Green", "#00ff00"), ("Blue", "#0000ff"), ("White", "#ffffff")):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _, v=value: self.solid_color(v))
            colors.add(button)
        outer.add(colors)
        note = Gtk.Label(label="Brightness scales the chosen color. Rainbow/random effects may ignore color and brightness.", xalign=0, wrap=True)
        outer.add(note)
        saved = Gtk.Box(spacing=8)
        saved.add(Gtk.Label(label="Preset"))
        self.preset = Gtk.ComboBoxText.new_with_entry()
        self.preset.set_hexpand(True)
        saved.pack_start(self.preset, True, True, 0)
        for label, action in (("Save", self.save_preset), ("Load", self.load_preset)):
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            saved.add(button)
        outer.add(saved)
        lcd = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        lcd_controls = Gtk.Box(spacing=8)
        self.lcd_mode = Gtk.ComboBoxText()
        self.lcd_mode.append("clock", "Clock")
        self.lcd_mode.append("cpu", "CPU temperature (°F)")
        self.lcd_mode.append("image", "Custom image")
        self.lcd_mode.set_active_id("clock")
        lcd_controls.add(self.lcd_mode)
        self.lcd_file = Gtk.FileChooserButton(title="Choose an LCD image", action=Gtk.FileChooserAction.OPEN)
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        image_filter.add_pixbuf_formats()
        self.lcd_file.add_filter(image_filter)
        self.lcd_file.set_hexpand(True)
        lcd_controls.pack_start(self.lcd_file, True, True, 0)
        self.lcd_rotation = Gtk.ComboBoxText()
        for angle in (0, 90, 180, 270):
            self.lcd_rotation.append(str(angle), f"{angle}°")
        self.lcd_rotation.set_active_id("0")
        lcd_controls.add(self.lcd_rotation)
        self.lcd_start = Gtk.Button(label="Show on LCD")
        self.lcd_start.connect("clicked", self.start_lcd)
        lcd_controls.add(self.lcd_start)
        self.lcd_stop = Gtk.Button(label="Stop")
        self.lcd_stop.set_sensitive(False)
        self.lcd_stop.connect("clicked", self.stop_lcd)
        lcd_controls.add(self.lcd_stop)
        lcd.add(lcd_controls)
        self.lcd_status = Gtk.Label(label="480 × 480 display. Keep Spaced RGB open while streaming; images are fitted with black borders.", xalign=0, wrap=True)
        lcd.add(self.lcd_status)
        lcd_expander = Gtk.Expander(label="Lian Li LCD")
        lcd_expander.add(lcd)
        outer.add(lcd_expander)
        self.details = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.details.set_left_margin(8)
        self.details.set_right_margin(8)
        details_scroll = Gtk.ScrolledWindow(height_request=100)
        details_scroll.add(self.details)
        expander = Gtk.Expander(label="Details / device access")
        expander.add(details_scroll)
        outer.add(expander)
        self.refresh_presets()
        self.show_all()
        self.scan()

    def report(self, message, details=""):
        logger.info("%s%s", message, "\n" + details if details else "")
        self.status.set_text(message)
        self.details.get_buffer().set_text(details or message)

    def work(self, function, done, foreground=True):
        if self.closing:
            return
        if self.busy:
            if foreground:
                self.pending.append((function, done))
                logger.info("Queued user operation behind active device operation")
            return
        self.busy = True
        widgets = (self.scan_button, self.apply_button, self.off_button, self.setup_button, self.lcd_start)
        if foreground:
            for widget in widgets:
                widget.set_sensitive(False)
        future = self.pool.submit(function)
        def complete():
            if self.closing:
                return GLib.SOURCE_REMOVE
            self.busy = False
            if foreground:
                for widget in widgets:
                    widget.set_sensitive(True)
            try:
                done(future.result())
            except Exception as exc:
                logger.exception("Device operation failed")
                self.report(str(exc))
            if self.pending:
                next_function, next_done = self.pending.popleft()
                self.work(next_function, next_done)
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(complete))

    def scan(self):
        self.report("Detecting controllers… this can take up to 40 seconds.")
        def finished(result):
            targets, warnings = result
            old = self.selection()
            for child in self.devices.get_children():
                child.destroy()
            self.rows = {}
            for target in targets:
                row = Gtk.Box(spacing=10, margin=5)
                check = Gtk.CheckButton(label=target["name"], active=target["key"] in old)
                check.set_hexpand(True)
                check.set_tooltip_text(target["kind"] + " — " + target["key"])
                check.connect("toggled", self.sync_selection)
                row.pack_start(check, True, True, 0)
                effects = Gtk.ComboBoxText()
                for effect in target["effects"]:
                    effects.append(effect, effect)
                effects.set_active_id(old.get(target["key"], "Static"))
                row.add(effects)
                self.rows[target["key"]] = (check, effects)
                self.devices.add(row)
            self.devices.show_all()
            self.sync_selection()
            message = f"{len(targets)} lighting targets detected. Select devices, choose a color, then Apply."
            if warnings:
                message += " Access details below."
            if not targets:
                message = "No accessible lighting targets. Run Hardware Enable to configure access and rescan."
            self.report(message, "\n".join(warnings) or "Devices detected. OpenRGB SDK is connected on localhost.\nCooler targets are detected over USB; access is checked when applying.")
        self.work(self.engine.discover, finished)

    def selection(self):
        return {key: effects.get_active_id() for key, (check, effects) in self.rows.items() if check.get_active()}

    def toggle_all(self, button):
        if self.selecting:
            return
        self.selecting = True
        for check, _ in self.rows.values():
            check.set_active(button.get_active())
        self.selecting = False
        self.sync_selection()

    def sync_selection(self, *_):
        if self.selecting:
            return
        self.selecting = True
        selected = sum(check.get_active() for check, _ in self.rows.values())
        self.select_all.set_sensitive(bool(self.rows))
        self.select_all.set_active(bool(self.rows) and selected == len(self.rows))
        self.select_all.set_inconsistent(0 < selected < len(self.rows))
        self.selecting = False

    def solid_color(self, value):
        color = Gdk.RGBA()
        color.parse(value)
        self.color.set_rgba(color)
        self.hex_entry.set_text(value)
        for check, effects in self.rows.values():
            if check.get_active():
                effects.set_active_id("Static")
        self.report(f"Solid {value} selected. Press Apply selected to send it.")

    def color_hex(self):
        c = self.color.get_rgba()
        return "#" + "".join(f"{round(v * 255):02x}" for v in (c.red, c.green, c.blue))

    def apply(self, off=False):
        selected = self.selection()
        if not selected:
            self.report("Select at least one lighting target first.")
            return
        if off:
            selected = {key: "Off" for key in selected}
        color, brightness = self.hex_entry.get_text().strip(), round(self.brightness.get_value())
        try:
            rgb(color, brightness)
        except ValueError as exc:
            self.report(str(exc))
            return
        rgba = Gdk.RGBA()
        rgba.parse("#" + color.removeprefix("#"))
        self.color.set_rgba(rgba)
        self.report("Sending lighting commands…")
        def finished(results):
            failures = sum(not ok for _, ok, _ in results)
            self.report(f"Commands sent to {len(results) - failures} target(s); {failures} failed.",
                        f"Color {color}; brightness {brightness}%\n" + "\n".join(f"{name}: {message}" for name, _, message in results))
        self.work(lambda: self.engine.apply(selected, color, brightness), finished)

    def refresh_presets(self):
        try:
            current = self.preset.get_child().get_text()
            self.preset.remove_all()
            for name in sorted(self.presets.read()):
                self.preset.append_text(name)
            self.preset.get_child().set_text(current)
        except Exception as exc:
            self.report(f"Could not read presets: {exc}")

    def save_preset(self, _):
        try:
            color = self.hex_entry.get_text().strip()
            rgb(color, round(self.brightness.get_value()))
            self.presets.save(self.preset.get_child().get_text(), "#" + color.removeprefix("#"),
                              round(self.brightness.get_value()), self.selection())
            self.refresh_presets()
            self.report("Preset saved.")
        except Exception as exc:
            self.report(str(exc))

    def load_preset(self, _):
        try:
            data = self.presets.load(self.preset.get_child().get_text())
            color = Gdk.RGBA()
            color.parse(data["color"])
            self.color.set_rgba(color)
            self.brightness.set_value(data["brightness"])
            missing = set(data["selected"]) - set(self.rows)
            for key, (check, effects) in self.rows.items():
                effect = data["selected"].get(key)
                valid = bool(effect and effects.set_active_id(effect))
                check.set_active(valid)
                if effect and not valid:
                    missing.add(key)
            message = "Preset loaded. Press Apply selected to send it."
            if missing:
                message += f" {len(missing)} saved target(s) unavailable; skipped."
            self.report(message)
            self.sync_selection()
        except Exception as exc:
            self.report(f"Could not load preset: {exc}")

    def start_lcd(self, _):
        mode, path = self.lcd_mode.get_active_id(), self.lcd_file.get_filename()
        if mode == "image" and not path:
            self.lcd_status.set_text("Choose an image first.")
            return
        self.stop_lcd()
        self.lcd_settings = (mode, path, int(self.lcd_rotation.get_active_id()))
        self.lcd_active = True
        self.lcd_stop.set_sensitive(True)
        self.lcd_status.set_text("Starting LCD stream…")
        self.lcd_timer = GLib.timeout_add_seconds(1, self.lcd_tick)
        self.lcd_tick()

    def stop_lcd(self, *_):
        self.lcd_active = False
        if self.lcd_timer:
            GLib.source_remove(self.lcd_timer)
            self.lcd_timer = None
        self.pool.submit(self.engine.stop_lcd)
        self.lcd_stop.set_sensitive(False)
        self.lcd_status.set_text("LCD streaming stopped. The display may retain its last frame or go blank.")

    def lcd_tick(self):
        if not self.lcd_active or self.closing:
            return GLib.SOURCE_REMOVE
        if self.busy:
            return GLib.SOURCE_CONTINUE
        settings = self.lcd_settings
        def send():
            try:
                self.engine.show_lcd(*settings)
                return None
            except Exception as exc:
                logger.exception("LCD update failed")
                return str(exc)
        def done(error):
            if not self.lcd_active:
                return
            if error:
                self.stop_lcd()
                self.lcd_status.set_text("LCD: " + error)
            else:
                self.lcd_status.set_text("Streaming to LCD. Keep Spaced RGB open; updates pause briefly during lighting commands.")
        self.work(send, done, foreground=False)
        return GLib.SOURCE_CONTINUE

    def hardware_setup(self, _):
        def setup():
            script = ROOT / "scripts/setup-hardware.sh"
            command = ["pkexec", "bash", str(script), str(os.getuid())]
            if os.environ.get("FLATPAK_ID"):
                # /app is private to the sandbox. Pass the bundled script as
                # one argument to the host shell, with the UID as $1.
                command = ["flatpak-spawn", "--host", "pkexec", "bash", "-c",
                           script.read_text(), "spaced-rgb-hardware-enable", str(os.getuid())]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("Hardware Enable did not finish: " + (result.stderr.strip() or "authentication cancelled"))
            self.engine.refresh_hardware()
            return result.stdout.strip()
        def finished(result):
            logger.info("Hardware Enable completed: %s", result)
            self.scan()
        self.work(setup, finished)

    def on_close(self, *_):
        self.stop_lcd()
        self.closing = True
        # Queue cleanup after any outstanding SDK/HID operation, then let GTK
        # leave immediately. Executor threads finish before Python exits.
        self.pool.submit(self.engine.close)
        self.pool.shutdown(wait=False)
        return False


def main():
    app = Gtk.Application(application_id="org.spacedlinux.RGB", flags=Gio.ApplicationFlags.FLAGS_NONE)
    def activate(application):
        if application.get_active_window():
            application.get_active_window().present()
        else:
            Window(application)
    app.connect("activate", activate)
    return app.run(None)
