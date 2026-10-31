"""Run on the host with xvfb-run; exercises GTK without lighting writes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tempfile import TemporaryDirectory
from spaced_rgb.app import Window, Gtk, Gdk, GLib
from spaced_rgb.core import Presets

window = Window(None)
loop = GLib.MainLoop()
attempts = 0
failure = []

def inspect():
    global attempts
    attempts += 1
    if window.busy and attempts < 200:
        return GLib.SOURCE_CONTINUE
    try:
        assert not window.busy, "Device discovery did not finish"
        assert len(window.rows) >= 2, window.status.get_text()
        with TemporaryDirectory() as directory:
            window.presets = Presets(Path(directory) / "presets.json")
            first = next(iter(window.rows))
            window.rows[first][0].set_active(True)
            window.preset.get_child().set_text("Test blue")
            window.save_preset(None)
            assert window.presets.load("Test blue")["selected"] == {first: "Static"}
            window.rows[first][0].set_active(False)
            window.load_preset(None)
            assert window.rows[first][0].get_active()
            # Loading never submits a lighting write; only an explicit Apply can.
            assert not window.busy
        window.get_window().process_updates(True)
        shot = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, window.get_allocated_width(), window.get_allocated_height())
        shot.savev(str(Path(__file__).resolve().parents[1] / ".backend/preview.png"), "png", [], [])
        print("GTK smoke passed: real discovery, selection, preset save/load, screenshot. No lighting writes.")
    except Exception as exc:
        failure.append(exc)
    finally:
        window.close()
        loop.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add(250, inspect)
loop.run()
if failure:
    raise failure[0]
