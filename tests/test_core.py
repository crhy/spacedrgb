import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from spaced_rgb.core import Engine, Presets, Target, identity, rgb, ROOT
from openrgb.utils import ModeColors


class ColorsAndPresets(unittest.TestCase):
    def test_brightness_and_validation(self):
        self.assertEqual(rgb("#ff8000", 50), (128, 64, 0))
        for color, brightness in (("#abcd", 100), ("gg0000", 10), ("ffffff", 101), ("ffffff", True)):
            with self.assertRaises(ValueError):
                rgb(color, brightness)

    def test_preset_roundtrip_atomic_and_no_unsafe_names(self):
        with TemporaryDirectory() as temp:
            store = Presets(Path(temp) / "presets.json")
            store.save("../../blue", "#0000ff", 40, {"stable-id": "Static"})
            self.assertEqual(store.load("../../blue")["selected"], {"stable-id": "Static"})
            self.assertEqual(list(Path(temp).iterdir()), [store.path])
            self.assertFalse(store.path.with_suffix(".tmp").exists())
            with self.assertRaises(ValueError):
                store.save("bad", "#000000", 100, {})

    def test_malformed_preset_rejected(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "presets.json"
            path.write_text(json.dumps({"version": 1, "presets": {
                "broken": {"color": "#123456", "brightness": 100, "selected": {"id": 42}}}}))
            with self.assertRaises(ValueError):
                Presets(path).load("broken")


class Lighting(unittest.TestCase):
    def make_device(self):
        dev = Mock()
        dev.name = "Test RAM"
        dev.type = SimpleNamespace(name="DRAM")
        dev.metadata = SimpleNamespace(serial="serial", location="bus:1")
        dev.modes = [SimpleNamespace(name="Direct", color_mode=ModeColors.PER_LED),
                     SimpleNamespace(name="Rainbow", color_mode=ModeColors.NONE)]
        return dev

    def test_static_and_off_only_lighting(self):
        dev = self.make_device()
        Engine._apply_openrgb(dev, "Static", (30, 50, 70))
        self.assertEqual(vars(dev.set_color.call_args.args[0]), {"red": 30, "green": 50, "blue": 70})
        Engine._apply_openrgb(dev, "Off", (30, 50, 70))
        self.assertEqual(vars(dev.set_color.call_args.args[0]), {"red": 0, "green": 0, "blue": 0})
        self.assertEqual([call[0] for call in dev.method_calls], ["set_mode", "set_color", "set_mode", "set_color"])

    def test_random_effect_does_not_pretend_to_accept_color(self):
        dev = self.make_device()
        Engine._apply_openrgb(dev, "Effect: Rainbow", (1, 2, 3))
        dev.set_mode.assert_called_once_with("Rainbow")
        dev.set_color.assert_not_called()

    def test_no_static_mode_errors_before_writing(self):
        dev = self.make_device()
        dev.modes = dev.modes[1:]
        with self.assertRaises(ValueError):
            Engine._apply_openrgb(dev, "Off", (0, 0, 0))
        self.assertEqual(dev.method_calls, [])

    def test_missing_device_never_falls_back_to_index(self):
        engine = Engine()
        dev = self.make_device()
        engine.targets["new-device"] = (Target("new-device", "RAM", "Dram", ("Static",)), ("openrgb", dev, None))
        result = engine.apply({"old-device": "Static"}, "#ffffff", 100)
        self.assertFalse(result[0][1])
        self.assertEqual(dev.method_calls, [])

    def test_device_failures_are_isolated_and_unknown_modes_rejected(self):
        engine = Engine()
        bad, good = self.make_device(), self.make_device()
        bad.set_mode.side_effect = PermissionError("denied")
        for key, dev in (("a", bad), ("b", good)):
            engine.targets[key] = (Target(key, key, "Dram", ("Static",)), ("openrgb", dev, None))
        self.assertEqual([r[1] for r in engine.apply({"a": "Static", "b": "Static"}, "#ffffff", 100)], [False, True])
        good.reset_mock()
        self.assertFalse(engine.apply({"b": "Effect: mystery"}, "#ffffff", 100)[0][1])
        self.assertEqual(good.method_calls, [])

    def test_breathing_applies_selected_color(self):
        dev = self.make_device()
        dev.modes.append(SimpleNamespace(name="Breathing", color_mode=ModeColors.PER_LED))
        Engine._apply_openrgb(dev, "Effect: Breathing", (0, 255, 0))
        dev.set_mode.assert_called_once_with("Breathing")
        self.assertEqual(vars(dev.set_color.call_args.args[0]), {"red": 0, "green": 255, "blue": 0})

    def test_dropped_connection_recovers_all_handles_by_identity(self):
        from openrgb.utils import OpenRGBDisconnected
        engine = Engine()
        old = [self.make_device(), self.make_device()]
        fresh = [self.make_device(), self.make_device()]
        for devices in (old, fresh):
            devices[1].metadata = SimpleNamespace(serial="second", location="bus:2")
        engine.client = Mock()
        old_client = engine.client
        keys = []
        for dev in old:
            key = identity("openrgb", dev.name, dev.type.name, dev.metadata.serial, dev.metadata.location)
            keys.append(key)
            engine.targets[key] = (Target(key, dev.name, "Dram", ("Static",)), ("openrgb", dev, None))
        old[0].set_mode.side_effect = OpenRGBDisconnected()
        new_client = SimpleNamespace(devices=list(reversed(fresh)))
        with patch.object(engine, "_connect", side_effect=lambda: setattr(engine, "client", new_client)) as connect:
            results = engine.apply(dict.fromkeys(keys, "Static"), "#00ff00", 100)
        self.assertTrue(all(result[1] for result in results))
        old_client.disconnect.assert_called_once()
        connect.assert_called_once()
        old[1].set_mode.assert_not_called()
        for dev in fresh:
            dev.set_mode.assert_called_once_with("Direct")
            dev.set_color.assert_called_once()

    def test_reconnect_never_controls_replacement_device(self):
        from openrgb.utils import OpenRGBDisconnected
        engine = Engine()
        dev, replacement = self.make_device(), self.make_device()
        replacement.metadata = SimpleNamespace(serial="replacement", location="other-bus")
        engine.client = Mock()
        engine.targets["missing"] = (Target("missing", "RAM", "Dram", ("Static",)), ("openrgb", dev, None))
        dev.set_mode.side_effect = OpenRGBDisconnected()
        with patch.object(engine, "_connect", side_effect=lambda: setattr(
                engine, "client", SimpleNamespace(devices=[replacement]))):
            result = engine.apply({"missing": "Static"}, "#ffffff", 100)
        self.assertFalse(result[0][1])
        replacement.set_mode.assert_not_called()
        self.assertIn("disconnected", result[0][2])

    def test_connection_recovery_is_bounded_and_error_is_readable(self):
        from openrgb.utils import OpenRGBDisconnected
        engine = Engine()
        dev = self.make_device()
        engine.targets["ram"] = (Target("ram", "RAM", "Dram", ("Static",)), ("openrgb", dev, None))
        dev.set_mode.side_effect = OpenRGBDisconnected()
        with patch.object(engine, "_reconnect_openrgb") as reconnect:
            result = engine.apply({"ram": "Static"}, "#ffffff", 100)
        reconnect.assert_called_once()
        self.assertEqual(dev.set_mode.call_count, 2)
        self.assertFalse(result[0][1])
        self.assertIn("connection lost", result[0][2])
        self.assertNotIn("Hardware Enable", result[0][2])

    def test_duplicate_identities_skipped_in_discovery(self):
        engine = Engine()
        engine.client = SimpleNamespace(devices=[self.make_device(), self.make_device()])
        with patch.object(engine, "_connect"), patch.object(engine, "_coolers", return_value=[]):
            # Preload the supported HID driver without scanning anything else.
            import sys, smbus2
            sys.modules.setdefault("smbus", smbus2)
            from liquidctl.driver.ga2_lcd import GA2LCD
            targets, warnings = engine.discover()
        self.assertEqual(targets, [])
        self.assertTrue(any("Ambiguous identity" in w for w in warnings))

    def test_cooler_off_uses_all_zone_adapter_without_initialization(self):
        engine = Engine()
        dev = Mock()
        engine.targets["pump"] = (Target("pump", "Pump", "Cooler", ("Static", "Off")), ("liquidctl", dev, "pump"))
        with patch.object(engine, "_coolers", return_value=[dev]), patch("spaced_rgb.ga2_lighting.set_lighting") as send:
            result = engine.apply({"pump": "Off"}, "#ff0000", 80)
        self.assertTrue(result[0][1])
        send.assert_called_once_with(dev.device, "pump", "static", (0, 0, 0))
        dev.initialize.assert_not_called()
        dev.connect.assert_not_called()

    def test_rebooted_cooler_uses_fresh_handle_matching_serial(self):
        engine = Engine()
        old, fresh = Mock(), Mock()
        old.serial_number = fresh.serial_number = "cooler-serial"
        fresh.connect.return_value.__enter__ = Mock(return_value=fresh)
        fresh.connect.return_value.__exit__ = Mock(return_value=False)
        engine.targets["pump"] = (Target("pump", "Pump", "Cooler", ("Static",)),
                                  ("liquidctl", old, "pump"))
        with patch.object(engine, "_coolers", return_value=[fresh]), patch("spaced_rgb.ga2_lighting.set_lighting") as send:
            result = engine.apply({"pump": "Static"}, "#00ff00", 100)
        self.assertTrue(result[0][1])
        old.connect.assert_not_called()
        send.assert_called_once_with(fresh.device, "pump", "static", (0, 255, 0))
        with patch.object(engine, "_coolers", return_value=[]):
            result = engine.apply({"pump": "Static"}, "#00ff00", 100)
        self.assertFalse(result[0][1])
        old.connect.assert_not_called()

    def test_identity_is_stable_and_distinguishes_locations(self):
        self.assertEqual(identity("RAM", "bus1"), identity("RAM", "bus1"))
        self.assertNotEqual(identity("RAM", "bus1"), identity("RAM", "bus2"))

    def test_hardware_refresh_stops_owned_backend_and_disconnects_client(self):
        engine = Engine()
        process, client = Mock(), Mock()
        process.poll.return_value = None
        engine.process, engine.client = process, client
        with patch.object(engine, "_reachable", return_value=False):
            engine.refresh_hardware()
        client.disconnect.assert_called_once_with()
        process.terminate.assert_called_once_with()
        self.assertIsNone(engine.client)
        self.assertIsNone(engine.process)

    def test_new_backend_waits_for_delayed_device_detection(self):
        engine = Engine()
        engine.client = Mock()
        ram, gpu = self.make_device(), self.make_device()
        gpu.name = "Test GPU"
        inventories = iter([[], [ram], [ram, gpu], [ram, gpu], [ram, gpu], [ram, gpu]])
        engine.client.update.side_effect = lambda: setattr(engine.client, "devices", next(inventories))
        with patch("spaced_rgb.core.time.monotonic", side_effect=range(30)), \
                patch("spaced_rgb.core.time.sleep"):
            engine._wait_for_devices()
        self.assertEqual(engine.client.devices, [ram, gpu])
        self.assertGreaterEqual(engine.client.update.call_count, 4)

    def test_hardware_refresh_reclaims_only_exact_bundled_server(self):
        import os
        import signal
        binary = str(ROOT / ".backend/squashfs-root/usr/bin/OpenRGB")
        args = [binary, "--server", "--server-host", "127.0.0.1",
                "--server-port", "6742", "--noautoconnect"]
        entries = []
        for pid, command, uid in (("101", args, os.getuid()),
                                  ("102", ["/usr/bin/OpenRGB"] + args[1:], os.getuid()),
                                  ("103", args, os.getuid() + 1),
                                  ("104", args + ["--profile", "other"], os.getuid())):
            entry = Mock(name=pid)
            entry.name = pid
            entry.stat.return_value.st_uid = uid
            child = Mock()
            child.read_bytes.return_value = ("\0".join(command) + "\0").encode()
            entry.__truediv__ = Mock(return_value=child)
            entries.append(entry)
        engine = Engine()
        with patch("spaced_rgb.core.Path.glob", return_value=entries), \
                patch("spaced_rgb.core.os.kill") as kill, \
                patch.object(engine, "_reachable", return_value=False):
            engine.refresh_hardware()
        kill.assert_called_once_with(101, signal.SIGTERM)

    def test_hardware_refresh_preserves_external_server(self):
        engine = Engine()
        engine.client = Mock()
        with patch("spaced_rgb.core.Path.glob", return_value=[]), \
                patch("spaced_rgb.core.os.kill") as kill, \
                patch.object(engine, "_reachable") as reachable:
            engine.refresh_hardware()
        kill.assert_not_called()
        reachable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
