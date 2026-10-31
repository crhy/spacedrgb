#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
rgb_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
rgb_dest=${XDG_DATA_HOME:-$HOME/.local/share}/spaced-rgb
if [[ -n ${FLATPAK_ID:-} ]]; then
    exec flatpak-spawn --host bash "$rgb_source/scripts/install.sh" "$@"
fi
python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' || {
    echo "Install python3-gi gir1.2-gtk-3.0 python3-pip first." >&2
    exit 1
}
mkdir -p "$rgb_source/.vendor" "$rgb_source/.backend"
# Isolated user-owned dependencies; never alter distro Python packages.
if [[ ! -f $rgb_source/.vendor/.requirements ]] || ! cmp -s "$rgb_source/requirements.txt" "$rgb_source/.vendor/.requirements"; then
    python3 -m pip install --target "$rgb_source/.vendor" --upgrade --no-deps -r "$rgb_source/requirements.txt"
    cp "$rgb_source/requirements.txt" "$rgb_source/.vendor/.requirements"
fi
if [[ ! -x $rgb_source/.backend/squashfs-root/AppRun ]]; then
    if [[ $(uname -m) != x86_64 ]]; then
        echo "This backend bundle requires x86_64." >&2
        exit 1
    fi
    rgb_download=$(mktemp -d)
    trap 'rm -rf -- "$rgb_download"' EXIT
    curl --fail --location --retry 3 --output "$rgb_download/OpenRGB.AppImage" \
        https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_1.1/OpenRGB_1.1_x86_64_8e16de2.AppImage
    printf '%s  %s\n' d556c2273b0ef863dbee7ffdb438304b382add5d685c93c60758b42de3b0268d "$rgb_download/OpenRGB.AppImage" | sha256sum -c -
    chmod +x "$rgb_download/OpenRGB.AppImage"
    (cd "$rgb_source/.backend"; "$rgb_download/OpenRGB.AppImage" --appimage-extract >/dev/null)
fi
mkdir -p "$rgb_dest" "$HOME/.local/bin" "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
if [[ $rgb_source != "$rgb_dest" ]]; then
    cp -a --remove-destination "$rgb_source/spaced_rgb" "$rgb_source/run.py" "$rgb_source/scripts" "$rgb_source/.vendor" \
        "$rgb_source/.backend" "$rgb_source/docs" "$rgb_source/README.md" "$rgb_source/requirements.txt" "$rgb_source/THIRD_PARTY.md" "$rgb_source/LICENSE" "$rgb_dest/"
fi
# Quote paths for the shell launcher, including home directories containing spaces.
printf '#!/bin/bash\nexec /usr/bin/python3 %q "$@"\n' "$rgb_dest/run.py" > "$HOME/.local/bin/spaced-rgb"
chmod +x "$HOME/.local/bin/spaced-rgb"
printf '#!/bin/bash\nexec bash %q "$@"\n' "$rgb_dest/scripts/ga2-firmware.sh" > "$HOME/.local/bin/spaced-ga2-firmware"
chmod +x "$HOME/.local/bin/spaced-ga2-firmware"
# Desktop Exec accepts double-quoted arguments; escape reserved characters.
rgb_exec=${rgb_dest//\\/\\\\}
rgb_exec=${rgb_exec//\"/\\\"}
rgb_exec=${rgb_exec//\$/\\\$}
rgb_exec=${rgb_exec//\`/\\\`}
rgb_exec=${rgb_exec//%/%%}
cat > "${XDG_DATA_HOME:-$HOME/.local/share}/applications/spaced-rgb.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Spaced RGB
Comment=Control RAM, graphics card and cooler lighting
Exec=/usr/bin/python3 "$rgb_exec/run.py"
Icon=preferences-desktop-color
Terminal=false
Categories=Settings;HardwareSettings;
StartupNotify=true
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" || true
printf 'Installed Spaced RGB. Launch from the menu or %s/.local/bin/spaced-rgb\n' "$HOME"
command -v ffmpeg >/dev/null || printf 'Optional LCD support: sudo apt install ffmpeg\n'
