# SPDX-License-Identifier: GPL-3.0-or-later
"""Lighting and display adapters. No cooling or voltage controls."""
from __future__ import annotations

import hashlib
import json
import logging
import fcntl
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, asdict

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "spaced-rgb"
logger = logging.getLogger(__name__)

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "spaced-rgb"


def rgb(hex_color, brightness):
    value = hex_color.removeprefix("#")
    if len(value) != 6 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Use a six-digit color, for example #3388ff")
    if type(brightness) is not int or not 0 <= brightness <= 100:
        raise ValueError("Brightness must be between 0 and 100")
    return tuple(round(int(value[i:i + 2], 16) * brightness / 100) for i in (0, 2, 4))


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    kind: str
    effects: tuple[str, ...]


class Presets:
    def __init__(self, path=None):
        self.path = Path(path) if path else CONFIG / "presets.json"

    def read(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("presets"), dict):
            raise ValueError("Unrecognized preset file")
        return data["presets"]

    def save(self, name, color, brightness, selected):
        if not name.strip() or len(name) > 80:
            raise ValueError("Enter a preset name (up to 80 characters)")
        rgb(color, brightness)
        if not selected or not all(isinstance(k, str) and isinstance(v, str) for k, v in selected.items()):
            raise ValueError("Select at least one device")
        data = self.read()
        data[name.strip()] = {"color": color, "brightness": brightness, "selected": selected}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "presets": data}, indent=2) + "\n")
        temporary.replace(self.path)

    def load(self, name):
        data = self.read()[name]
        rgb(data["color"], data["brightness"])
        if not isinstance(data.get("selected"), dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data["selected"].items()
        ):
            raise ValueError("Invalid device selection in preset")
        return data


class Engine:
    def __init__(self):
        self.client = None
        self.process = None
        self.log = None
        self.targets = {}
        self.warnings = []
        self.lcd = None

    @staticmethod
    def _reachable():
        try:
            with socket.create_connection(("127.0.0.1", 6742), timeout=0.3):
                return True
        except OSError:
            return False

    def _connect(self):
        from openrgb import OpenRGBClient
        started = False
        if self.client:
            self.client.update()
            return
        if not self._reachable():
            app = ROOT / ".backend/squashfs-root/AppRun"
            if not app.exists():
                raise RuntimeError("OpenRGB backend missing. Run scripts/install.sh first.")
            if not self.process or self.process.poll() is not None:
                STATE.mkdir(parents=True, exist_ok=True)
                if self.log:
                    self.log.close()
                self.log = (STATE / "openrgb.log").open("a")
                self.process = subprocess.Popen(
                    [str(app), "--server", "--server-host", "127.0.0.1", "--server-port", "6742", "--noautoconnect"],
                    stdout=self.log, stderr=subprocess.STDOUT,
                )
                started = True
            deadline = time.monotonic() + 40
            while not self._reachable():
                if self.process.poll() is not None:
                    raise RuntimeError(f"OpenRGB stopped. See {STATE / 'openrgb.log'}")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"OpenRGB is still scanning. Try Detect again. Log: {STATE / 'openrgb.log'}")
                time.sleep(0.25)
        self.client = OpenRGBClient(address="127.0.0.1", port=6742, name="Spaced RGB", protocol_version=3)
        if started:
            self._wait_for_devices()

    def _wait_for_devices(self):
        # The SDK listener starts before hardware detection finishes. Wait for
        # a nonempty list to settle instead of publishing the initial empty list.
        deadline = time.monotonic() + 40
        previous = None
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            self.client.update()
            devices = tuple((dev.name, dev.type.name) for dev in self.client.devices)
            now = time.monotonic()
            if devices != previous:
                previous, stable_since = devices, now
            elif devices and now - stable_since >= 2:
                return
            time.sleep(0.25)

    def refresh_hardware(self):
        """Refresh access after udev setup, keeping the application window open."""
        # A backend left by an earlier app instance can still be scanning with
        # the old permissions. Only reclaim our exact bundled server command.
        orphan = []
        if self.process is None:
            binary = str(ROOT / ".backend/squashfs-root/usr/bin/OpenRGB")
            expected = [binary, "--server", "--server-host", "127.0.0.1",
                        "--server-port", "6742", "--noautoconnect"]
            for entry in Path("/proc").glob("[0-9]*"):
                try:
                    if entry.stat().st_uid != os.getuid():
                        continue
                    args = os.fsdecode((entry / "cmdline").read_bytes()).rstrip("\0").split("\0")
                    if args == expected:
                        orphan.append(int(entry.name))
                except (OSError, ValueError):
                    continue
        owned = self.process is not None or bool(orphan)
        self.close()
        self.process = None
        for pid in orphan:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if owned:
            deadline = time.monotonic() + 5
            while self._reachable():
                if time.monotonic() >= deadline:
                    raise RuntimeError("RGB backend is still stopping; try Detect again")
                time.sleep(0.1)
        logger.info("Hardware access refreshed; bundled backend restarted=%s", owned)

    @staticmethod
    def _coolers():
        # liquidctl eagerly imports SMBus drivers. Its C smbus dependency is
        # unnecessary here; smbus2 provides the same import API. Only GA2 HID
        # discovery is invoked, never liquidctl SMBus scanning.
        import smbus2
        sys.modules.setdefault("smbus", smbus2)
        from liquidctl.driver.ga2_lcd import GA2LCD
        return list(GA2LCD.find_supported_devices())

    def discover(self):
        self.stop_lcd()
        self.targets = {}
        self.warnings = []
        found = []
        try:
            self._connect()
            for dev in self.client.devices:
                if dev.type.name not in {"DRAM", "GPU", "MOTHERBOARD", "COOLER", "LEDSTRIP", "CASE"}:
                    continue
                metadata = dev.metadata
                key = identity("openrgb", dev.name, dev.type.name,
                               getattr(metadata, "serial", ""), getattr(metadata, "location", ""))
                effects = ("Static", "Off") + tuple("Effect: " + m.name for m in dev.modes)
                target = Target(key, dev.name, dev.type.name.title(), effects)
                found.append((target, ("openrgb", dev, None)))
        except Exception as exc:
            self.warnings.append(f"OpenRGB: {exc}")
            if self.client:
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
        try:
            coolers = self._coolers()
            from liquidctl.driver.ga2_lcd import _PUMP_LIGHTING_MODES
            from .ga2_lighting import FAN_LIGHTING_MODES
            for dev in coolers:
                stable = dev.serial_number or str(dev.address)
                for channel, modes in (("pump", _PUMP_LIGHTING_MODES), ("fan", FAN_LIGHTING_MODES)):
                    key = identity("ga2lcd", stable, channel)
                    effects = ("Static", "Off") + tuple("Effect: " + mode for mode in modes if mode != "static")
                    target = Target(key, "Lian Li GA II LCD — " + channel + " LEDs", "Cooler", effects)
                    found.append((target, ("liquidctl", dev, channel)))
        except Exception as exc:
            self.warnings.append(f"Cooler: {exc}")
        counts = {}
        for target, _ in found:
            counts[target.key] = counts.get(target.key, 0) + 1
        for target, handle in found:
            if counts[target.key] != 1:
                self.warnings.append(f"Ambiguous identity: {target.name}; skipped to avoid controlling the wrong device")
                continue
            self.targets[target.key] = (target, handle)
        if not Path("/dev/i2c-0").exists():
            self.warnings.append("RAM/GPU access needs i2c-dev. Use Hardware Enable to configure access and rescan.")
        return [asdict(t) for t, _ in self.targets.values()], self.warnings

    def _reconnect_openrgb(self):
        """Replace stale SDK handles, matching identities rather than indices."""
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self._connect()
        current = {}
        for dev in self.client.devices:
            metadata = dev.metadata
            key = identity("openrgb", dev.name, dev.type.name,
                           getattr(metadata, "serial", ""), getattr(metadata, "location", ""))
            current.setdefault(key, []).append(dev)
        for key, (target, (backend, _, channel)) in list(self.targets.items()):
            if backend != "openrgb":
                continue
            matches = current.get(key, [])
            if len(matches) == 1:
                self.targets[key] = (target, (backend, matches[0], channel))
            else:
                del self.targets[key]
        logger.info("OpenRGB connection recovered; device handles refreshed")

    @staticmethod
    def _apply_openrgb(dev, effect, color):
        from openrgb.utils import RGBColor, ModeColors
        if effect in {"Static", "Off"}:
            # Prefer an explicit constant mode over a device-specific custom
            # mode, which can be animated on some controllers.
            mode = next((m for m in dev.modes if m.name.lower() in {"direct", "static", "solid", "fixed"}
                         and m.color_mode in {ModeColors.PER_LED, ModeColors.MODE_SPECIFIC}), None)
            if mode is None:
                raise ValueError("No supported constant-color mode; choose a listed hardware effect")
            dev.set_mode(mode.name)
            dev.set_color(RGBColor(*(color if effect == "Static" else (0, 0, 0))))
        else:
            name = effect.removeprefix("Effect: ")
            mode = next((m for m in dev.modes if m.name == name), None)
            if mode is None:
                raise ValueError("This effect is no longer available; detect devices again")
            dev.set_mode(name)
            if mode.color_mode in {ModeColors.PER_LED, ModeColors.MODE_SPECIFIC}:
                dev.set_color(RGBColor(*color))

    def apply(self, selected, hex_color, brightness):
        color = rgb(hex_color, brightness)
        from openrgb.utils import OpenRGBDisconnected
        results = []
        recovered = False
        for key, effect in selected.items():
            if key not in self.targets:
                results.append((key, False, "Device is no longer connected; detect devices again"))
                continue
            target, (backend, dev, channel) = self.targets[key]
            try:
                if effect not in target.effects:
                    raise ValueError("Unsupported lighting effect")
                if backend == "openrgb":
                    try:
                        self._apply_openrgb(dev, effect, color)
                    except (OpenRGBDisconnected, ConnectionError, TimeoutError):
                        if recovered:
                            raise
                        recovered = True
                        logger.warning("OpenRGB connection lost; reconnecting before retrying lighting")
                        self._reconnect_openrgb()
                        if key not in self.targets:
                            raise RuntimeError("Device disconnected or identity ambiguous; detect devices again")
                        _, (_, dev, _) = self.targets[key]
                        self._apply_openrgb(dev, effect, color)
                else:
                    self.stop_lcd()
                    # USB paths and handles can change after a controller reboot.
                    # Match the original identity before opening a fresh handle.
                    stable = dev.serial_number or str(dev.address)
                    matches = [candidate for candidate in self._coolers()
                               if (candidate.serial_number or str(candidate.address)) == stable]
                    if len(matches) != 1:
                        raise RuntimeError("Cooler disconnected or identity ambiguous; detect devices again")
                    current = matches[0]
                    STATE.mkdir(parents=True, exist_ok=True)
                    with (STATE / "ga2-firmware.lock").open("a") as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise RuntimeError("Cooler is in use by another Spaced RGB operation") from None
                        from .ga2_lighting import set_lighting
                        mode = "static" if effect in {"Static", "Off"} else effect.removeprefix("Effect: ")
                        set_lighting(current.device, channel, mode,
                                     (0, 0, 0) if effect == "Off" else color)
                logger.info("Lighting sent: target=%s backend=%s channel=%s effect=%s color=%s",
                            target.name, backend, channel, effect, color)
                results.append((target.name, True, "Lighting command sent"))
            except Exception as exc:
                logger.exception("Lighting failed: target=%s backend=%s channel=%s effect=%s",
                                 target.name, backend, channel, effect)
                message = str(exc) or type(exc).__name__
                if isinstance(exc, OpenRGBDisconnected):
                    message = "OpenRGB connection lost; try Detect devices again"
                elif isinstance(exc, PermissionError):
                    message += ". Run Hardware Enable to configure access"
                results.append((target.name, False, message))
        return results

    def show_lcd(self, mode, path=None, rotation=0):
        from .lcd import LCDController
        if self.lcd is None:
            self.lcd = LCDController()
        self.lcd.show(mode, path, rotation)

    def stop_lcd(self):
        if self.lcd is not None:
            self.lcd.close()

    def close(self):
        self.stop_lcd()
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()
