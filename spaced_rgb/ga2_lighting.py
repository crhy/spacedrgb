# SPDX-License-Identifier: GPL-3.0-or-later
"""GA II LCD lighting reports, without firmware queries or cooling writes."""
import logging
import time

logger = logging.getLogger(__name__)

# GA II LCD/Vision wire modes. liquidctl's fan table reverses the effect
# numbering (static happens to remain 3). L-Connect's fan packet uses the
# same effect numbers as the pump; see FanControl.LianLi/docs/lighting.md.
FAN_LIGHTING_MODES = {
    "meteor": 6, "runway": 5, "breathing": 4, "static": 3,
    "rainbow-morph": 2, "rainbow": 1,
}


def lighting_report(channel, mode, color):
    from liquidctl.driver.ga2_lcd import _PUMP_LIGHTING_MODES

    # All four palette entries must carry the chosen color. Scope 2 selects
    # every pump zone; scope 0 only addresses the inner lighting.
    palette = list(color) * 4
    if channel == "pump":
        command = 0x83
        payload = [2, _PUMP_LIGHTING_MODES[mode], 4, 2] + palette + [0, 0, 0]
    elif channel == "fan":
        command = 0x85
        payload = [FAN_LIGHTING_MODES[mode], 4, 2] + palette + [0, 0, 0, 0, 24]
    else:
        raise ValueError("Unknown GA II lighting channel")
    report = bytes([1, command, 0, 0, 0, len(payload)] + payload)
    return report.ljust(64, b"\0")


def set_lighting(transport, channel, mode, color):
    report = lighting_report(channel, mode, color)
    # Use the discovered HID interface and backend, not a cached open handle.
    device = transport.api.device()
    try:
        device.open_path(transport.path)
        # Discard full stale status reports, with a bound for noisy devices.
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline and device.read(512, 20):
            pass
        written = device.write(report)
        if written != len(report):
            raise OSError(f"Incomplete GA II lighting write: {written}/64 bytes")
        # Match the lifetime of the direct test that visibly changed both
        # channels. Keep the interface open and service input for one second
        # before closing it. Replies can be unrelated LCD/status reports;
        # none is treated as proof that the lighting was applied.
        reply_commands = set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            reply = device.read(512, 100)
            if len(reply) >= 2:
                reply_commands.add(f"0x{reply[1]:02x}")
        logger.info(
            "GA II HID lighting write: channel=%s mode=%s color=%s path=%s bytes=%s replies=%s",
            channel, mode, color, transport.path, written,
            ",".join(sorted(reply_commands)) or "none",
        )
    finally:
        device.close()
