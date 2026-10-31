#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
if [[ $EUID != 0 ]]; then
    echo "Run with pkexec or sudo, passing the desktop user's numeric UID." >&2
    exit 1
fi
rgb_uid=${1:-${SUDO_UID:-}}
if [[ ! $rgb_uid =~ ^[0-9]+$ ]] || (( rgb_uid < 1000 || rgb_uid == 65534 )); then
    echo "A regular desktop user's numeric UID is required." >&2
    exit 1
fi
getent passwd "$rgb_uid" >/dev/null
umask 022
install -d -m 755 /etc/udev/rules.d
cat > /etc/udev/rules.d/99-spaced-rgb.rules <<RULES
# Spaced RGB: access for UID $rgb_uid only. ASUS Aura + Lian Li GA II LCD.
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0b05", ATTRS{idProduct}=="19af", OWNER="$rgb_uid", MODE="0660"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="7395", OWNER="$rgb_uid", MODE="0660"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0b05", ATTR{idProduct}=="19af", OWNER="$rgb_uid", MODE="0660"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0416", ATTR{idProduct}=="7395", OWNER="$rgb_uid", MODE="0660"
# AMD RAM SMBus and NVIDIA GPU RGB adapters on this machine.
SUBSYSTEM=="i2c-dev", ATTRS{name}=="SMBus PIIX4*", OWNER="$rgb_uid", MODE="0660"
SUBSYSTEM=="i2c-dev", ATTRS{name}=="NVIDIA i2c adapter*", OWNER="$rgb_uid", MODE="0660"
RULES
# Devuan's sysvinit kmod service reads /etc/modules; no systemd required.
touch /etc/modules
if ! grep -Eq '^[[:space:]]*i2c[-_]dev([[:space:]#]|$)' /etc/modules; then
    printf '\n# Spaced RGB hardware access\ni2c-dev\n' >> /etc/modules
fi
modprobe i2c-dev
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=usb
udevadm trigger --action=change --subsystem-match=hidraw
udevadm trigger --action=change --subsystem-match=i2c-dev
udevadm settle
printf 'RGB access configured for UID %s. Spaced RGB will refresh hardware detection automatically.\n' "$rgb_uid"
