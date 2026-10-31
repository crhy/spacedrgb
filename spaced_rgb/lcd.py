# SPDX-License-Identifier: GPL-3.0-or-later
"""GA II LCD display streaming; no cooling controls or USB resets.

Protocol/encoder reference: sbulav/galahad-linux-control (MIT), see THIRD_PARTY.md.
"""
from contextlib import ExitStack
from datetime import datetime
import fcntl
import math
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .ga2_firmware import STATE, open_device, packets, read_version, select_device, validate_device_version

SIZE = (480, 480)


def cpu_temperature(root=Path("/sys/class/hwmon")):
    """Read CPU silicon sensors, excluding GPU, RAM and board thermistors."""
    candidates = []
    for directory in Path(root).glob("hwmon*"):
        try:
            driver = (directory / "name").read_text().strip()
        except OSError:
            continue
        if driver not in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
            continue
        for sensor in directory.glob("temp*_input"):
            try:
                label = sensor.with_name(sensor.name.replace("_input", "_label")).read_text().strip()
                priority = 0 if label == "Tdie" or label.startswith("Package id") else 1
                if label not in ("Tdie", "Tctl") and not label.startswith("Package id"):
                    continue
                value = float(sensor.read_text()) / 1000
                if math.isfinite(value) and -20 <= value <= 125:
                    candidates.append((priority, value))
            except (OSError, ValueError):
                continue
    if not candidates:
        raise RuntimeError("CPU temperature unavailable: no readable CPU package sensor in /sys/class/hwmon")
    priority = min(item[0] for item in candidates)
    return max(value for rank, value in candidates if rank == priority)


def display_font(size, bold=False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def temperature_frame(celsius, rotation=0):
    if not math.isfinite(celsius) or not -20 <= celsius <= 125:
        raise ValueError("Invalid CPU temperature")
    frame = Image.new("RGB", SIZE, "#050b10")
    draw = ImageDraw.Draw(frame)
    accent = "#56f0b0"
    draw.rounded_rectangle((18, 18, 461, 461), radius=34, outline="#1d3c38", width=2)
    draw.line((180, 78, 300, 78), fill=accent, width=5)
    draw.text((240, 120), "CPU TEMPERATURE", font=display_font(25, True), fill="#a8c2be", anchor="mm")
    number = str(round(celsius * 9 / 5 + 32))
    size = 166
    while draw.textlength(number, font=display_font(size, True)) > 410:
        size -= 2
    draw.text((240, 245), number, font=display_font(size, True), fill="#f0fff9", anchor="mm")
    draw.text((240, 352), "°F", font=display_font(52), fill=accent, anchor="mm")
    return frame.rotate(-rotation)


def image_frame(path, rotation=0):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", image.size, "black")
        background.alpha_composite(image)
        image = ImageOps.contain(background.convert("RGB"), SIZE)
    frame = Image.new("RGB", SIZE, "black")
    frame.paste(image, ((480 - image.width) // 2, (480 - image.height) // 2))
    return frame.rotate(-rotation)


def clock_frame(rotation=0, now=None):
    now = now or datetime.now()
    frame = Image.new("RGB", SIZE, "black")
    draw = ImageDraw.Draw(frame)
    def font(size):
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except OSError:
            return ImageFont.load_default(size=size)
    draw.text((240, 210), now.strftime("%H:%M:%S"), font=font(78), fill="white", anchor="mm")
    draw.text((240, 285), now.strftime("%a %d %b %Y"), font=font(28), fill="#aaaaaa", anchor="mm")
    return frame.rotate(-rotation)


def encode_frame(frame):
    encoder = shutil.which("ffmpeg")
    if not encoder:
        raise RuntimeError("LCD support needs ffmpeg: install it with sudo apt install ffmpeg")
    result = subprocess.run([
        encoder, "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", "480x480", "-i", "pipe:0", "-frames:v", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-tune", "zerolatency", "-profile:v", "baseline", "-level", "3.0",
        "-crf", "25", "-threads", "1", "-x264-params",
        "cabac=0:ref=1:deblock=0:0:0:analyse=0:0:me=dia:subme=0:keyint=24:keyint_min=2:scenecut=0:bframes=0:mbtree=0",
        "-f", "h264", "pipe:1"], input=frame.convert("RGB").tobytes(), capture_output=True, timeout=10)
    if result.returncode or not result.stdout:
        raise RuntimeError("LCD encoding failed: " + result.stderr.decode(errors="replace")[-800:])
    return result.stdout


def configure_display(device):
    # 0x0c: LCDSetting (vendor brightness command), software rotation, test off,
    # black test RGB, 24 fps decoder. See docs/ga2-firmware-protocol.md.
    packet = next(iter(packets(0x0c, bytes((4, 50, 0, 0, 0, 0, 0, 24)))))
    if device.write(packet) != len(packet):
        raise RuntimeError("Short LCD mode USB write; streaming stopped")
    device.read(512, 100)


def send_frame(device, data, modern=False):
    if not data or len(data) > 2 * 1024 * 1024:
        raise ValueError("LCD frame must be between 1 byte and 2 MiB")
    if modern:
        reports = []
        for number, offset in enumerate(range(0, len(data), 501)):
            chunk = data[offset:offset + 501]
            header = b"\x03\x0d" + len(data).to_bytes(4, "big")
            header += number.to_bytes(3, "big") + len(chunk).to_bytes(2, "big")
            reports.append((header + chunk).ljust(512, b"\0"))
    else:
        reports = packets(0x0d, data)
    for packet in reports:
        if device.write(packet) != len(packet):
            raise RuntimeError("Short LCD USB write; streaming stopped")
    # Vendor sends the whole frame before reading one response. Waiting on
    # each chunk starves the decoder. Transport success is not visual proof.
    device.read(512, 20)


class LCDController:
    def __init__(self):
        self.session = None
        self.device = None
        self.modern = False
        self.cached_key = None
        self.cached_data = None

    def show(self, mode, path=None, rotation=0):
        if rotation not in (0, 90, 180, 270):
            raise ValueError("Unsupported LCD rotation")
        if mode == "clock":
            data = encode_frame(clock_frame(rotation))
        elif mode == "cpu":
            data = encode_frame(temperature_frame(cpu_temperature(), rotation))
        elif mode == "image":
            image = Path(path).resolve()
            key = (str(image), image.stat().st_mtime_ns, rotation)
            if key != self.cached_key:
                self.cached_data = encode_frame(image_frame(image, rotation))
                self.cached_key = key
            data = self.cached_data
        else:
            raise ValueError("Unsupported LCD mode")
        try:
            if self.device is None:
                self.connect()
            send_frame(self.device, data, modern=self.modern)
        except Exception:
            # A disconnected handle stays failed. Never automatically reconnect
            # and resume after a controller reboot.
            self.close()
            raise

    def connect(self):
        # Match liquidctl's Linux backend. libusb HID detaches the kernel
        # driver and invalidates the hidraw paths used by lighting commands.
        try:
            import hidraw as hid
        except ImportError:
            import hid
        STATE.mkdir(parents=True, exist_ok=True)
        session = ExitStack()
        try:
            lock = session.enter_context((STATE / "ga2-firmware.lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Cooler firmware updater is busy; LCD streaming stopped") from None
            entry = select_device(hid)
            device = session.enter_context(open_device(hid, entry))
            version, _ = read_version(device)
            validate_device_version(version)
            try:
                firmware = tuple(int(part) for part in version.split(",")[-1].split("."))
            except ValueError:
                raise RuntimeError("Unrecognized LCD firmware version") from None
            configure_display(device)
            self.modern = firmware >= (1, 5)
            self.device = device
            self.session = session
        except Exception:
            session.close()
            raise

    def close(self):
        session, self.session = self.session, None
        self.device = None
        if session is not None:
            session.close()
