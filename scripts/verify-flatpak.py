#!/usr/bin/env python3
"""Verify packaged dependencies and encode a frame without opening hardware."""
import sys
import subprocess
from pathlib import Path

root = Path("/app/share/spaced-rgb")
sys.path[:0] = [str(root), str(root / ".vendor")]
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import hid
import usb.core
import openrgb
from spaced_rgb.lcd import temperature_frame, encode_frame
from spaced_rgb import core

frame = temperature_frame(45)
assert frame.size == (480, 480)
encoded = encode_frame(frame)
assert encoded.startswith((b"\x00\x00\x00\x01", b"\x00\x00\x01"))
subprocess.run([str(root / ".backend/squashfs-root/AppRun"), "--version"], check=True)
print(f"Packaged GTK, hardware libraries and LCD encoder OK ({len(encoded)} bytes); no device commands sent")
