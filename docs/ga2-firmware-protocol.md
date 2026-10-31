# GA II LCD firmware protocol evidence

## Display mode (not firmware)

The same vendor assembly's `LCDControlSetting.ToBytes` defines eight bytes:
mode, brightness, rotation, test enable, test R/G/B, video FPS. `LCDControlMode`
maps LocalUI=0, Application=1, LocalH264=2, LocalAVI=3, LCDSetting=4,
LCDTest=5. `LCDControl` sends this with B command 0x0c; `SendH264Frame`
uses 0x0d. The vendor controller's `setScreenBrightness` uses **LCDSetting=4**,
not Application=1: payload `04 32 00 00 00 00 00 18` for 50% brightness,
software rotation, test off and 24 FPS. Send this once per connection, as a
brightness setting, rather than every frame. `SendH264Frame512` writes every
chunk consecutively, then reads one response (512 bytes, 20 ms) per frame.
No vendor source code is included.

On 2026-10-30 the earlier implementation produced a logo/image composite and
one-second HID driver reattachments from opening/closing hidapi-libusb per
frame. At 18:05:37 the kernel recorded an actual disconnect, temporary
0416:3f04 enumeration, then 0416:7395 firmware 1.6 returning. This establishes
a controller restart, but does not establish whether power, firmware or stream
handling caused it. The adapter now keeps a connection and updater lock for
the stream, releases both on stop/error/exit, and stops on a failed write rather
than reconnecting automatically. Lighting commands release the LCD connection
before opening liquidctl.

Physical verification on the same date: the user still saw the logo after both
a 60-second persistent-connection temperature test and a 45-second continuous
24 FPS H.264 test. Neither test caused USB re-enumeration. These changes have
not fixed the logo overlay, and the brief tests do not establish long-term
controller stability. The experimental continuous encoder is not shipped.
A separate 40-second built-in LCDTest white-screen command was sent and then
LCDSetting restored. The user reported a gray, flickering logo/display rather
than a clean white screen. This is consistent with the symptoms in the vendor
FAQ, but does not prove a panel fault or confirm successful test-mode entry.
The user also reported an unlit rear fan and a half-lit middle fan. Read-only
OpenRGB SDK inspection showed all three motherboard addressable RGB zones
configured with zero LEDs; fan wiring and required counts are unknown, so
those fan symptoms cannot yet be attributed to damaged hardware.

The [official GA II LCD FAQ](https://lian-li.com/faq/faq_galahad-ii-lcd/)
lists flickering/ghosting as a possible LCD hardware fault for which Lian Li
recommends a screen replacement. It also advises a firmly seated pump USB-C
connector and a direct motherboard USB 2.0 nine-pin connection for black-screen
or freezing problems. These are troubleshooting leads, not a confirmed diagnosis
of this unit. Check physical connections with the PC powered off and unplugged.

## Further LCD communication research (2026-10-30)

The LCD receives compressed video over USB HID, rather than framebuffer RGB
pixels or a desktop display connection. Lighting and screen control are separate
commands: turning LEDs off does not turn the LCD off or establish its health.
The rendered temperature image is opaque and contains no Lian Li logo. A logo
visible over it is therefore not intentional composition by our renderer; this
does not identify which firmware, decoder, panel or transport condition causes it.

Cross-checks against the vendor assembly and the maintained
[Linux AIO driver](https://github.com/sgtaziz/lian-li-linux/blob/main/crates/lianli-devices/src/hydroshift_lcd/controller.rs):

| Operation | Report / command | Details |
| --- | --- | --- |
| Firmware identity | A / 0x86 | 64-byte output; two indexed input reports |
| Handshake / telemetry | A / 0x81 | 64-byte output; fan/pump RPM and coolant temperature reply |
| LCD settings | B / 0x0c | Always 1024-byte output, including on firmware 1.6 |
| H.264 access unit | C / 0x0d on GA II >=1.5 | 512-byte chunks, 501-byte payload; full frame before reading |
| LCD availability | B / 0x17 | Empty request; one-byte zero reply means available |
| JPEG image | B / 0x0e in the upstream shared driver | 1024-byte chunks; not exposed in the inspected vendor GA II class |

The vendor initialization queries firmware, obtains a handshake, starts recurring
one-second handshakes and input reads, waits two seconds, then starts streaming.
The upstream driver additionally selects **Application=1**, while the vendor
brightness setter selects **LCDSetting=4**. This discrepancy warrants testing;
it is not evidence that Application mode alone fixes the overlay. Our current
adapter does not implement the recurring handshake sequence.

An upstream [GA II report with the same USB ID and firmware 1.6](https://github.com/sgtaziz/lian-li-linux/issues/54)
describes a stuck default logo and then grey flickering; the original GA II
reporter confirmed operation with software libx264 and 512-byte mode in 0.5.4.
Later HydroShift comments in that issue concern a different model and must not
be treated as GA II confirmation. Relevant changes include
[initialization and report-format fixes](https://github.com/sgtaziz/lian-li-linux/commit/1b7c5e634fbc5d2feec1faf44428f85585928fce)
and [H.264 framing and stale-response handling](https://github.com/sgtaziz/lian-li-linux/commit/70fc153a1662d4c2902779b68b0b04fe0d7e2acf).
These provide a software troubleshooting lead rather than proof of the cause here.

Additional local observations:

- A 90-second Application=1 test retained the current encoder and C framing.
  USB device number stayed 30 throughout; the user still saw the logo.
  Mode selection alone did not fix this unit.
- A subsequent 60-iteration test used two startup handshakes one second apart,
  Application=1, and a matched 0x81 response before each H.264 image. USB device
  number remained 30; the user still saw the logo. Adding this initialization
  and telemetry sequence did not fix the overlay with the current encoder.
- An experimental 45-iteration JPEG stream used complete opaque 480×480
  baseline JPEG images, B/0x0e framing and the same initialization/handshakes.
  USB device number remained 30, but no matched 0x0e acknowledgment arrived.
  The user thought the temperature looked cleaner, then reported previous
  digits remaining visible. The temperature image alone was inconclusive.
- A subsequent 35-iteration test sent an opaque orange baseline JPEG with black
  `JPEG / 123` lettering, using the same B/0x0e path. The user confirmed that
  exact pattern appeared, establishing JPEG acceptance despite the absence of
  a matched acknowledgment. Residual temperature digits and part of the logo
  remained visible over it. USB device number remained 30. Ghosting therefore
  persists across both image and video paths; changing the temperature renderer
  or merely switching to JPEG is not a demonstrated fix. This does not alone
  distinguish a controller/firmware fault from physical panel image retention.
  No separate screen-clear command was found in the vendor GA II or upstream
  controller paths inspected. The manufacturer's
  [GA II LCD FAQ](https://lian-li.com/faq/faq_galahad-ii-lcd/)
  explicitly associates flickering/ghosting with possible LCD MOSFET damage and
  recommends contacting support for screen replacement. That is a relevant
  hardware troubleshooting lead, not a confirmed diagnosis of this unit.
- Three 0x81 queries produced matched 64-byte replies. Fan RPM was 1020;
  pump RPM was 2760–2790 and coolant temperature approximately 37°C.
  These are reported telemetry, not an independent cooling-performance test.
- A 0x17 query returned a 512-byte B response with one-byte payload zero:
  the firmware reports the LCD available. This does not prove correct decoding
  or a healthy physical display.

The diagnostic scripts are kept outside the application in the user's cache.
None of these checks upload firmware, change fan/pump PWM, or reset USB.
USB writes completing successfully are never sufficient evidence of a clean image.

## Firmware

`Galahad2VisionController.onInitialize` selects `SendH264Frame512` for firmware
>=1.5. These C reports have ID 03, size 512, the same 11-byte header as B,
and at most 501 payload bytes. Older firmware uses B video reports. The LCD
adapter checks the running version to choose the format; firmware upload
continues to use B reports regardless of version.

Inspected on 2026-10-30, using official L-Connect 3 2.1.29 installer:
https://lianli-update-2025.lianli-cn.com/L3_CX/20260924-L-Connect%203-x64-v2.1.29-bc599de7.exe
linked from https://lian-li.com/l-connect3/.

Protocol facts were established by inspecting these managed classes, using
ILSpy on Linux. Vendor code/binaries are not included in this repository:

- `LConnect3cs.Utils.Firmware.Galahad2VisionFirmwareUpgrader`
- `LConnectCore.Products.Galahad2Vision.BCommandPacket`
- `LConnectCore.Products.Galahad2Vision.ACommandPacket`
- `LConnectCore.Products.Galahad2Vision.Galahad2VisionDevice`
- `LConnectCore.Products.Galahad2Vision.FirmwareVersion`

The vendor updater pauses its controller service with manual mode, waits 3s,
opens its existing HID path, discards pending reads, writes firmware reports
with command 11, reads a response (512-byte buffer, 10s timeout) per report,
and sleeps 10ms between reports. After 2s it sends command 10 with payload 01.
It then waits for USB reinsertion. Manual mode coordinates L-Connect processes;
Linux users must close other controllers. This tool verifies the running version
rather than treating reinsertion alone as success.

All output B reports are 1024 bytes, zero padded:

| Offset | Field |
| --- | --- |
| 0 | Report ID 02 |
| 1 | Command 0b upload / 0a activate |
| 2–5 | Full payload size, unsigned big endian |
| 6–8 | Zero-based chunk index, unsigned big endian |
| 9–10 | Current chunk length, unsigned big endian |
| 11 onward | Up to 1013 data bytes |

A firmware query is a 64-byte report beginning `01 86`, followed by zeros.
Two A input reports give version and build date. A headers: report ID, command,
reserved byte, BE16 index, one-byte payload size; data starts at offset 6.
The version fields are chip, boot, type, model A, model B, software, user version.

The vendor uploader does not decode acknowledgment status fields. During the
successful 2026-10-30 hardware update (serial 00000055FA92, 1.3 to 1.6), all 619
512-byte responses exactly echoed the first 512 bytes of their output report.
This implementation requires that echo, including the chunk index and size,
and rejects stale responses. Exact post-reboot version matching is also required.
No claim of flash readback or cryptographic device attestation is made.

Pinned vendor image (626212 bytes), GA II LCD 1.6 / V2.01.041:
https://lianli-update-2025.s3.ap-southeast-1.amazonaws.com/FW_0618/HH015CHS_N9H26HSSQ_GALAHADII-Vision_V201041_pid7395.bin

SHA256: `68e2d11c409e75b1e25bf9c2e84b891bcdc18ac735bafa67a0ee25a218b2d9d3`

Official firmware listing: https://lian-li.com/l-connect3/l3_update_manually/

Do not use Nuvoton fan-controller ISP utilities or assume the generic N9H26 BSP
SPI erase/write example matches this product. Recovery PID 0416:3f04 and the
vendor's separate boot-update utility are outside this implementation.
