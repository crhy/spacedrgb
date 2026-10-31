from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from spaced_rgb.lcd import LCDController, clock_frame, configure_display, encode_frame, image_frame, send_frame, cpu_temperature, temperature_frame


class LCD(unittest.TestCase):
    def test_stream_keeps_connection_and_configures_only_once(self):
        controller = LCDController()
        device = Mock()
        calls = Mock()
        with TemporaryDirectory() as directory, \
                patch("spaced_rgb.lcd.STATE", Path(directory)), \
                patch("spaced_rgb.lcd.select_device", return_value={"path": b"cooler"}), \
                patch("spaced_rgb.lcd.open_device") as opened, \
                patch("spaced_rgb.lcd.read_version", return_value=("N9,01,HS,SQ,CA_II-Vision,V2.01.041,1.6", None)), \
                patch("spaced_rgb.lcd.validate_device_version"), \
                patch("spaced_rgb.lcd.cpu_temperature", return_value=60), \
                patch("spaced_rgb.lcd.temperature_frame") as render, \
                patch("spaced_rgb.lcd.encode_frame", return_value=b"video"), \
                patch("spaced_rgb.lcd.configure_display", calls.mode), \
                patch("spaced_rgb.lcd.send_frame", calls.frame):
            opened.return_value.__enter__.return_value = device
            for _ in range(2):
                controller.show("cpu", rotation=90)
            render.assert_called_with(60, 90)
            self.assertEqual([call[0] for call in calls.mock_calls],
                             ["mode", "frame", "frame"])
            calls.frame.assert_called_with(device, b"video", modern=True)
            opened.assert_called_once()
            opened.return_value.__exit__.assert_not_called()
            controller.close()
            opened.return_value.__exit__.assert_called_once()

    def test_disconnect_closes_session_and_propagates_error(self):
        controller = LCDController()
        controller.device = Mock()
        controller.session = Mock()
        session = controller.session
        with patch("spaced_rgb.lcd.encode_frame", return_value=b"video"), \
                patch("spaced_rgb.lcd.send_frame", side_effect=OSError("disconnected")):
            with self.assertRaisesRegex(OSError, "disconnected"):
                controller.show("clock")
        session.close.assert_called_once()
        self.assertIsNone(controller.device)
        self.assertIsNone(controller.session)
        controller.close()
        session.close.assert_called_once()

    def test_modern_video_reports_reassemble(self):
        device = Mock()
        device.write.side_effect = len
        data = bytes(range(256)) * 5
        send_frame(device, data, modern=True)
        restored = b""
        for number, call in enumerate(device.write.call_args_list):
            report = call.args[0]
            self.assertEqual(len(report), 512)
            self.assertEqual(report[:2], b"\x03\x0d")
            self.assertEqual(int.from_bytes(report[2:6], "big"), len(data))
            self.assertEqual(int.from_bytes(report[6:9], "big"), number)
            length = int.from_bytes(report[9:11], "big")
            self.assertLessEqual(length, 501)
            restored += report[11:11 + length]
            self.assertEqual(report[11 + length:], bytes(501 - length))
        self.assertEqual(restored, data)
        device.read.assert_called_once_with(512, 20)

    def test_vendor_brightness_packet(self):
        device = Mock()
        device.write.side_effect = len
        configure_display(device)
        packet = device.write.call_args.args[0]
        self.assertEqual(len(packet), 1024)
        self.assertEqual(packet[:11], bytes.fromhex("02 0c 00000008 000000 0008"))
        self.assertEqual(packet[11:19], bytes((4, 50, 0, 0, 0, 0, 0, 24)))
        self.assertEqual(packet[19:], bytes(1005))
        device.write.return_value = 2
        device.write.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "Short LCD mode"):
            configure_display(device)

    def test_cpu_sensor_selection_ignores_gpu_and_prefers_die(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (name, label, value) in enumerate((
                    ("amdgpu", "Tdie", "99000"), ("k10temp", "Tctl", "65000"),
                    ("k10temp", "Tdie", "55000"))):
                sensor = root / f"hwmon{index}"
                sensor.mkdir()
                for filename, text in (("name", name), ("temp1_label", label), ("temp1_input", value)):
                    (sensor / filename).write_text(text)
            self.assertEqual(cpu_temperature(root), 55)
            (root / "hwmon2/temp1_input").write_text("nan")
            self.assertEqual(cpu_temperature(root), 65)
            (root / "hwmon1/temp1_input").unlink()
            with self.assertRaisesRegex(RuntimeError, "CPU temperature unavailable"):
                cpu_temperature(root)

    def test_temperature_converts_to_fahrenheit_and_rotates(self):
        with patch("spaced_rgb.lcd.ImageDraw.ImageDraw.text", autospec=True) as text:
            frame = temperature_frame(60)
            strings = [call.args[2] for call in text.call_args_list]
            self.assertIn("140", strings)
            self.assertIn("°F", strings)
        self.assertEqual(frame.size, (480, 480))
        self.assertEqual(temperature_frame(60, 90).tobytes(), temperature_frame(60).rotate(-90).tobytes())
        for invalid in (float("nan"), float("inf"), 200):
            with self.assertRaises(ValueError):
                temperature_frame(invalid)

    def test_image_preserves_aspect_and_transparency(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGBA", (100, 50), (0, 255, 0, 255)).save(path)
            frame = image_frame(path)
            self.assertEqual(frame.size, (480, 480))
            self.assertEqual(frame.getpixel((240, 20)), (0, 0, 0))
            self.assertEqual(frame.getpixel((240, 240)), (0, 255, 0))
            rotated = image_frame(path, 90)
            self.assertEqual(rotated.getpixel((20, 240)), (0, 0, 0))
            Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(path)
            self.assertEqual(image_frame(path).getpixel((240, 240)), (0, 0, 0))

    def test_lcd_packets_reassemble_and_never_use_firmware_commands(self):
        device = Mock()
        device.write.side_effect = len
        data = bytes(range(256)) * 9
        send_frame(device, data)
        reports = [call.args[0] for call in device.write.call_args_list]
        restored = b""
        for number, report in enumerate(reports):
            self.assertEqual(len(report), 1024)
            self.assertEqual(report[:2], b"\x02\x0d")
            self.assertEqual(int.from_bytes(report[2:6], "big"), len(data))
            self.assertEqual(int.from_bytes(report[6:9], "big"), number)
            restored += report[11:11 + int.from_bytes(report[9:11], "big")]
        self.assertEqual(restored, data)
        device.read.assert_called_once_with(512, 20)

    def test_short_write_stops_without_retry(self):
        device = Mock()
        device.write.return_value = 3
        with self.assertRaisesRegex(RuntimeError, "Short LCD"):
            send_frame(device, b"a" * 3000)
        device.write.assert_called_once()

    def test_bad_frame_rejected_before_usb(self):
        device = Mock()
        for data in (b"", b"x" * (2 * 1024 * 1024 + 1)):
            with self.assertRaises(ValueError):
                send_frame(device, data)
        device.write.assert_not_called()

    def test_missing_encoder_has_actionable_error(self):
        with patch("spaced_rgb.lcd.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "apt install ffmpeg"):
                encode_frame(clock_frame())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_clock_encodes_real_h264(self):
        data = encode_frame(clock_frame(now=datetime(2026, 10, 30, 12, 34, 56)))
        self.assertTrue(data.startswith(b"\0\0\0\x01"))
        self.assertLess(len(data), 100000)

    def test_image_encoder_cache_and_invalid_mode(self):
        controller = LCDController()
        with self.assertRaises(ValueError):
            controller.show("bad")
        with self.assertRaises(ValueError):
            controller.show("clock", rotation=45)
        # Stop before USB selection while still exercising cache invalidation.
        with TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGB", (10, 10)).save(path)
            with patch("spaced_rgb.lcd.encode_frame", return_value=b"h264") as encode, \
                    patch("spaced_rgb.lcd.select_device", side_effect=RuntimeError("no USB")):
                for rotation in (0, 0, 90):
                    with self.assertRaisesRegex(RuntimeError, "no USB"):
                        controller.show("image", path, rotation)
                self.assertEqual(encode.call_count, 2)
