import sys
import unittest
from unittest.mock import Mock, patch

import smbus2
sys.modules.setdefault("smbus", smbus2)

from spaced_rgb.ga2_lighting import lighting_report, set_lighting


class LightingReports(unittest.TestCase):
    def test_packets_match_visually_confirmed_green_test(self):
        pump = bytes([1, 0x83, 0, 0, 0, 19, 2, 3, 4, 2] + [0, 255, 0] * 4 + [0, 0, 0]).ljust(64, b"\0")
        fan = bytes([1, 0x85, 0, 0, 0, 20, 3, 4, 2] + [0, 255, 0] * 4 + [0, 0, 0, 0, 24]).ljust(64, b"\0")
        self.assertEqual(lighting_report("pump", "static", (0, 255, 0)), pump)
        self.assertEqual(lighting_report("fan", "static", (0, 255, 0)), fan)

    def test_scaled_color_and_black_fill_every_palette_slot(self):
        for color in ((0, 26, 0), (0, 0, 0)):
            self.assertEqual(lighting_report("pump", "static", color)[10:22], bytes(color * 4))
            self.assertEqual(lighting_report("fan", "static", color)[9:21], bytes(color * 4))

    def test_breathing_uses_single_color_mode_on_both_channels(self):
        for channel, offset, palette_offset in (("pump", 7, 10), ("fan", 6, 9)):
            report = lighting_report(channel, "breathing", (1, 2, 3))
            self.assertEqual(report[offset], 4)
            self.assertEqual(report[palette_offset:palette_offset + 12], bytes([1, 2, 3] * 4))

    def test_fan_wire_modes_follow_lconnect_numbering(self):
        for mode, expected in (("rainbow", 1), ("rainbow-morph", 2), ("static", 3),
                               ("breathing", 4), ("runway", 5), ("meteor", 6)):
            self.assertEqual(lighting_report("fan", mode, (1, 2, 3))[6], expected)

    def test_write_uses_fresh_path_and_drains_full_reports(self):
        transport = Mock(path=b"/dev/hidraw5")
        device = transport.api.device.return_value
        device.read.side_effect = [[1, 0x81], [], [1, 0x8e]]
        device.write.return_value = 64
        with patch("spaced_rgb.ga2_lighting.time.monotonic", side_effect=[0, 0, 0, 0, 0.5, 1.01]):
            set_lighting(transport, "pump", "static", (0, 255, 0))
        device.open_path.assert_called_once_with(b"/dev/hidraw5")
        self.assertEqual(device.read.call_args_list, [((512, 20),), ((512, 20),), ((512, 100),)])
        device.write.assert_called_once_with(lighting_report("pump", "static", (0, 255, 0)))
        device.close.assert_called_once_with()

    def test_settle_read_failure_is_reported_and_handle_closed(self):
        transport = Mock()
        device = transport.api.device.return_value
        device.read.side_effect = [[], OSError("USB disconnected")]
        device.write.return_value = 64
        with self.assertRaisesRegex(OSError, "USB disconnected"):
            set_lighting(transport, "fan", "static", (0, 255, 0))
        device.close.assert_called_once_with()

    def test_failed_or_short_writes_are_errors_and_close_handle(self):
        for count in (-1, 6, 63):
            transport = Mock()
            device = transport.api.device.return_value
            device.read.return_value = []
            device.write.return_value = count
            with self.assertRaises(OSError):
                set_lighting(transport, "fan", "static", (0, 0, 0))
            device.close.assert_called_once_with()
