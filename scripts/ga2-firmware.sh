#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
rgb_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -n ${FLATPAK_ID:-} ]]; then
    exec flatpak-spawn --host bash "$rgb_root/scripts/ga2-firmware.sh" "$@"
fi
export PYTHONPATH="$rgb_root/.vendor:$rgb_root${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m spaced_rgb.ga2_firmware "$@"
