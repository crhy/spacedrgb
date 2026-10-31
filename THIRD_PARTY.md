# Upstream software

GA II LCD fan effect numbering is cross-checked against the L-Connect protocol
research in [FanControl.LianLi's lighting documentation](https://github.com/lewisgibson/FanControl.LianLi/blob/main/docs/lighting.md)
and its `Galahad2LightingEncoder`: the fan mode is sent directly, with Breathing
at 4, rather than liquidctl's reversed fan effect table. No C# source is bundled.

Spaced RGB is GPL-3.0-or-later. Hardware protocols are reused from these upstream
projects through their public libraries/SDK, with an independent native adapter
for the LCD and firmware protocols. Preserve all upstream
licenses when redistributing their files; installed dependency dist-info folders
contain notices.

| Project | Version | License | Purpose / source |
| --- | --- | --- | --- |
| OpenRGB | 1.1, 8e16de2 | GPL-2.0-or-later | RGB hardware SDK server; https://openrgb.org/ and https://codeberg.org/OpenRGB/OpenRGB |
| openrgb-python | 0.3.8 | GPL-3.0 | SDK client; https://github.com/jath03/openrgb-python |
| liquidctl | 1.16.0 | GPL-3.0-or-later | GA II LCD HID lighting driver; https://github.com/liquidctl/liquidctl |
| smbus2 | 0.5.0 | MIT | Compatible smbus import; https://github.com/kplindegaard/smbus2 |
| hidapi | 0.15.0 | BSD-3-Clause (Python wrapper); HIDAPI BSD/GPL choices | HID transport; https://github.com/trezor/cython-hidapi |
| pyusb | 1.3.1 | BSD-3-Clause | USB driver dependencies; https://github.com/pyusb/pyusb |
| Pillow | 13.0.0 | MIT-CMU | liquidctl dependency; https://github.com/python-pillow/Pillow |
| galahad-linux-control | protocol/encoder reference | MIT, © 2025 Sergei Bulavintsev | LCD packet format and H.264 encoder settings adapted in spaced_rgb/lcd.py; https://github.com/sbulav/galahad-linux-control |
| colorlog | 6.12.0 | MIT | liquidctl dependency; https://github.com/borntyping/python-colorlog |
| crcmod | 1.7 | MIT | liquidctl dependency; https://crcmod.sourceforge.net/ |
| docopt | 0.6.2 | MIT | liquidctl dependency; https://github.com/docopt/docopt |

OpenRGB downloads: https://openrgb.org/releases.html . Installer pins the exact
1.1 x86_64 AppImage filename and SHA-256 observed for that artifact. This is a
reproducibility checksum, not an independently verified signature. OpenRGB's
separate-process backend and bundled libraries retain their own licenses.
SDK protocol 3 is negotiated for compatibility with the pinned Python client.

Liquidctl normally depends on the C `smbus` extension. This app installs smbus2
instead and aliases that import before loading liquidctl. It only enumerates the
GA2LCD HID class, never liquidctl's SMBus discovery. This avoids requiring Python
C development headers on rolling Spaced Linux.

Native LCD streaming invokes the system ffmpeg/libx264 executable. The Flatpak
bundles its encoder as described below. The protocol reference's MIT license
is preserved in [docs/galahad-linux-control-LICENSE](docs/galahad-linux-control-LICENSE).

## Flatpak LCD encoder

The Flatpak bundles FFmpeg 7.1.4 (GPL-2.0-or-later with GPL features enabled) and x264 (GPL-2.0-or-later). Sources and exact checksums are in `flatpak/io.github.crhy.SpacedRGB.json`: https://ffmpeg.org/ and https://www.videolan.org/developers/x264.html. OpenRGB is bundled unmodified from its published AppImage; its source is at https://codeberg.org/OpenRGB/OpenRGB. Python dependencies retain their license files in the bundle.
