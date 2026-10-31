# Spaced RGB

A native GTK 3 lighting app for Spaced Linux/MATE. Uses OpenRGB 1.1 for RAM,
ASUS Aura/motherboard headers and supported GPUs, and liquidctl 1.16.0 for
Lian Li GA II LCD pump/fan lighting. GPL-3.0-or-later.

## Install and launch

```bash
bash scripts/install.sh
~/.local/bin/spaced-rgb
```

Needs host Python 3, python3-gi, gir1.2-gtk-3.0, python3-pip, curl, and PolicyKit
(`pkexec`). Run install from the host terminal; inside VSCodium Flatpak it
forwards to the host automatically. No systemd, FUSE, root GUI, or background
service is required. The pinned OpenRGB x86_64 AppImage is extracted locally.
Python dependencies are isolated under `.vendor`. The app installs under
`~/.local/share/spaced-rgb` and appears in the desktop applications menu.
LCD streaming additionally needs `ffmpeg` with the libx264 encoder
(`sudo apt install ffmpeg`). Lighting works without it.

On the first run, click **Hardware Enable** and authenticate. The app refreshes
its bundled backend and detects devices automatically, keeping the window open.
Setup enables i2c-dev at boot through Devuan's `/etc/modules` and adds
`/etc/udev/rules.d/99-spaced-rgb.rules`. Rules grant your account access to the
specific ASUS Aura (0b05:19af), Lian Li GA II LCD (0416:7395), AMD PIIX4 SMBus
and NVIDIA I²C devices on this machine. It does not grant world-write access.
This permits direct RGB hardware access, so use a trusted account.

Equivalent terminal setup:

```bash
sudo bash ~/.local/share/spaced-rgb/scripts/setup-hardware.sh "$(id -u)"
```

Use **Select all** above the device list, or select individual targets, choose
an effect/color/brightness, and click **Apply selected**. The checkbox shows a
mixed state when only some targets are selected. Enter an exact RGB hex color
beside the picker, or use the **Red / Green / Blue / White** buttons. These
buttons also switch selected targets to Static mode. Pure green is `#00ff00`;
brightness scales its green channel without adding blue or red.
Each target can use a different effect. **Lights off** sends static black to
selected targets. Save named presets; loading fills the controls, and **Apply**
sends them. The app never automatically changes your lights at launch or on
preset load. Rainbow/random hardware effects can ignore chosen color/brightness.

Expand **Lian Li LCD**, choose **Clock**, **CPU temperature (°F)** or **Custom image**, choose a rotation,
and click **Show on LCD**. Images are fitted to 480 × 480 without stretching;
transparent areas and borders are black. Animated files use their first frame.
Keep the app open: it sends an H.264 frame about once per second. **Stop** ends
streaming; the display may retain its last frame or go blank. Lighting and LCD
operations share one worker so their USB writes do not overlap. A display error
stops streaming and appears beside the LCD controls. LCD settings are session-only.
The temperature screen shows a giant Fahrenheit reading from the CPU package/die
sensor, with green accents. A missing sensor stops the display with an error.
Streaming sends the vendor LCDSetting brightness command once per connection
(50% LCD brightness) and uses the
512-byte video format required by the vendor app on firmware 1.5 and newer;
older firmware uses 1024-byte video reports. The USB connection remains open
until Stop, an error, exit or a cooler lighting command. A failed stream stops
without automatically reconnecting or resetting the controller.

## Hardware and limitations

Detected on the development machine: ASUS TUF GAMING X670E-PLUS WIFI, ASUS RTX
4070 Ti SUPER (10de:2705 / 1043:895b), two ENE DRAM RGB controllers, and
Lian Li GA II LCD (0416:7395), updated from firmware 1.3 to 1.6.
Presence in the UI does not prove a command
was accepted by the hardware; SDK/HID command submission cannot verify light output.

GA II LCD uses liquidctl's matching driver and verifies firmware before setting
LEDs. The independent LCD adapter uses the display-stream command documented
by galahad-linux-control; it does not invoke firmware, cooling or voltage controls
or reset the USB device. Only the GA II LCD interface 1 is supported, and exactly
one matching cooler must be attached. Cooler fans wired to a motherboard ARGB header instead of the
cooler's controller must be controlled through the ASUS motherboard target.
RAM must expose a supported RGB controller; RAM without LEDs cannot be lit.
Unconfigured ARGB strip lengths may need setting in OpenRGB's own GUI first.

The app starts its own OpenRGB SDK server on **127.0.0.1:6742** when needed, and
stops only the process it started on exit. Hardware Enable also restarts an exact
matching bundled server left by an earlier instance. An external SDK server is reused;
if devices are missing, restart that external server after setup. SDK has no authentication,
so keep external servers bound to localhost. Detection re-reads the SDK device list;
physical hotplug may require restarting the app/backend. Avoid running other RGB
writers against the same devices at the same time.

Stable device identifiers avoid applying saved selections by list index. Devices
with ambiguous identifiers are skipped. Presets live in
`~/.config/spaced-rgb/presets.json`. Command results and failures are recorded in
`~/.local/state/spaced-rgb/app.log`, with backend logs in
`~/.local/state/spaced-rgb/openrgb.log` (XDG paths honored). After a cooler reboot,
lighting commands reopen the matching USB device. Apply clicks during an LCD
update are queued until that update finishes.

## Develop and test

```bash
PYTHONPATH=.vendor python3 -m unittest discover -s tests -v
bash -n scripts/install.sh scripts/setup-hardware.sh
python3 run.py
```

Within the editor's Flatpak, prefix Python test/GUI commands with
`flatpak-spawn --host env PYTHONPATH=/absolute/path/to/.vendor`.
Tests mock RGB writes and check preset/device behavior; they do not flash LEDs.
Use `xvfb-run -a` before Python to include GTK control tests without a desktop.
The real encoder test runs when ffmpeg is installed; automated tests never write
to the cooler or LEDs.

To uninstall, close the app and remove `~/.local/share/spaced-rgb`,
`~/.local/bin/spaced-rgb`, `~/.local/bin/spaced-ga2-firmware` and
`~/.local/share/applications/spaced-rgb.desktop`.
Presets are kept separately. To revoke access, remove the above udev rules file
and the Spaced RGB i2c-dev entry in `/etc/modules`, reload udev rules and reboot.

See [THIRD_PARTY.md](THIRD_PARTY.md) for upstream software and licenses.

## GA II LCD firmware updater (Linux)

The separate, explicit command below supports **only USB 0416:7395, interface 1**,
N9/01/HS/SQ/CA_II-Vision boot identity, and the official 1.6 image
(V2.01.041). It uses Linux hidapi; no Wine, Windows, or systemd. It does not
support recovery-mode PID 3f04, bootloader updates, other coolers, or arbitrary
firmware. Do not try fan-controller firmware on this cooler.

Close other cooler-control applications before flashing, including Spaced RGB.
Keep power and USB connected throughout the update. The default command reads
the cooler version and validates the vendor download without uploading firmware:

```bash
bash scripts/ga2-firmware.sh
bash scripts/ga2-firmware.sh --flash
```

After installation, `~/.local/bin/spaced-ga2-firmware` runs the same tool.

Use `--image /path/to/vendor.bin` for an existing download. SHA256 and size must
match the pinned image. `--serial` selects one cooler if multiple are connected.
The tool uploads 619 acknowledged reports, activates the image, then requires
that the same serial/USB path report the exact target firmware version. Journals
are saved in `~/.local/state/spaced-rgb/ga2-firmware-*.jsonl`. It never retries
firmware writes or automatically resets a device after a failure. A failed
post-update check means the result is **unverified**, not successful; retain the
journal before attempting recovery. This is an independently implemented updater,
not a vendor-supported Linux utility. Protocol evidence is in
[docs/ga2-firmware-protocol.md](docs/ga2-firmware-protocol.md).

## Flatpak release

Install Spaced RGB from SpacedBazaar (the signed Spaced GitHub repository), or download the x86_64 bundle from [GitHub Releases](https://github.com/crhy/spacedrgb/releases).

```sh
flatpak install --user spaced-github io.github.crhy.SpacedRGB//stable
flatpak run io.github.crhy.SpacedRGB
```

Hardware Enable asks for administrator authentication on the host. The Flatpak needs device access for USB/HID and SMBus, local network access for the OpenRGB SDK, read access to image files, and permission to invoke host authentication. Only run one copy of Spaced RGB at a time.

To build with the GNOME 50 SDK installed:

```sh
flatpak-builder --force-clean --user --repo=repo build-dir flatpak/io.github.crhy.SpacedRGB.json
flatpak build-bundle --repo-url=https://crhy.github.io/spacedbazaar/flatpak-repo/ --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo repo SpacedRGB-0.1.0-x86_64.flatpak io.github.crhy.SpacedRGB stable
```

The build pins dependencies and includes an H.264 encoder for LCD streaming. Native development tests: `PYTHONPATH=.vendor xvfb-run -a python3 -m unittest discover -s tests`.
