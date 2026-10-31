# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import patch

from spaced_rgb import ga2_firmware as fw


class FakeDevice:
    def __init__(self, replies=()):
        self.replies = iter(replies)
        self.writes = []

    def write(self, packet):
        self.writes.append(packet)
        return len(packet)

    def read(self, length, timeout):
        return next(self.replies, [])


class FirmwareTests(unittest.TestCase):
    def test_vendor_packet_vector(self):
        data = bytes(range(256)) * 4
        first, last = list(fw.packets(11, data))
        self.assertEqual(first[:11].hex(), '020b0000040000000003f5')
        self.assertEqual(first[11:], data[:1013])
        self.assertEqual(last[:11].hex(), '020b00000400000001000b')
        self.assertEqual(last[11:22], data[1013:])
        self.assertEqual(last[22:], bytes(1002))
        self.assertEqual(len(first), 1024)

    def test_activation_vector(self):
        packet = next(fw.packets(10, b'\1'))
        self.assertEqual(packet[:12].hex(), '020a00000001000000000101')
        self.assertEqual(packet[12:], bytes(1012))

    def test_reject_image_and_wrong_device(self):
        with self.assertRaises(fw.UpdateError):
            fw.validate_image(bytes(fw.SIZE))
        with self.assertRaises(fw.UpdateError):
            fw.validate_device_version('N9,01,HS,SQ,HydroShift,1,1.3')

    def test_missing_ack_does_not_activate_or_retry(self):
        device = FakeDevice()
        with patch.object(fw, 'validate_image'), patch.object(fw, 'drain'):
            with self.assertRaisesRegex(fw.UpdateError, 'No response'):
                fw.transfer(device, bytes(2026), lambda *a, **k: None)
        self.assertEqual(len(device.writes), 1)
        self.assertEqual(device.writes[0][1], 11)

    def test_unrelated_response_does_not_activate(self):
        device = FakeDevice([b'bad response'])
        with patch.object(fw, 'validate_image'), patch.object(fw, 'drain'):
            with self.assertRaisesRegex(fw.UpdateError, 'Unexpected response'):
                fw.transfer(device, b'a', lambda *a, **k: None)
        self.assertEqual([p[1] for p in device.writes], [11])

    def test_upload_then_activate(self):
        device = FakeDevice([p[:512] for p in fw.packets(11, bytes(1014))])
        events = []
        with patch.object(fw, 'validate_image'), patch.object(fw, 'drain'), patch.object(fw.time, 'sleep'):
            fw.transfer(device, bytes(1014), lambda event, **kw: events.append(event))
        self.assertEqual([p[1] for p in device.writes], [11, 11, 10])
        self.assertEqual(events, ['chunk', 'chunk', 'uploaded', 'activating'])

    def test_stale_ack_does_not_activate(self):
        first = next(fw.packets(11, bytes(1014)))[:512]
        device = FakeDevice([first, first])
        with patch.object(fw, 'validate_image'), patch.object(fw, 'drain'), patch.object(fw.time, 'sleep'):
            with self.assertRaisesRegex(fw.UpdateError, 'Unexpected response to chunk 1'):
                fw.transfer(device, bytes(1014), lambda *a, **k: None)
        self.assertEqual([p[1] for p in device.writes], [11, 11])

    def test_short_write_does_not_activate(self):
        device = FakeDevice()
        device.write = lambda packet: 0
        with patch.object(fw, 'validate_image'), patch.object(fw, 'drain'):
            with self.assertRaisesRegex(fw.UpdateError, 'Short USB write'):
                fw.transfer(device, b'a', lambda *a, **k: None)

    def test_out_of_order_version_reports(self):
        version = fw.VERSION.encode()
        def report(index, data):
            return bytes([1, 0x86, 0, 0, index, len(data)]) + data
        device = FakeDevice([report(1, b'Jan 4 2024'), report(0, version)])
        with patch.object(fw, 'drain'):
            self.assertEqual(fw.read_version(device), (fw.VERSION, 'Jan 4 2024'))
        self.assertEqual(device.writes, [bytes([1, 0x86]) + bytes(62)])

    def test_ambiguous_selection_refused(self):
        class Hid:
            @staticmethod
            def enumerate(*a):
                return [{'interface_number': 1, 'serial_number': 'a'}] * 2
        with self.assertRaises(fw.UpdateError):
            fw.select_device(Hid)

    def test_reboot_requires_target_version(self):
        entry = {'path': b'port', 'serial_number': 'a'}
        with patch.object(fw, 'select_device', return_value=entry), \
             patch.object(fw, 'open_device') as op, \
             patch.object(fw, 'read_version', return_value=(fw.VERSION, 'build')), \
             patch.object(fw.time, 'sleep'):
            self.assertEqual(fw.verify_reboot(None, entry), (fw.VERSION, 'build'))


if __name__ == '__main__':
    unittest.main()
