# SPDX-License-Identifier: GPL-3.0-or-later
"""Native GA II LCD updater. Explicitly limited to one vendor firmware image."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

VID, PID = 0x0416, 0x7395
SIZE = 626212
SHA256 = '68e2d11c409e75b1e25bf9c2e84b891bcdc18ac735bafa67a0ee25a218b2d9d3'
VERSION = 'N9,01,HS,SQ,CA_II-Vision,V2.01.041,1.6'
URL = ('https://lianli-update-2025.s3.ap-southeast-1.amazonaws.com/FW_0618/'
       'HH015CHS_N9H26HSSQ_GALAHADII-Vision_V201041_pid7395.bin')
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'spaced-rgb'


class UpdateError(RuntimeError):
    pass


def validate_image(data):
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise UpdateError('Image does not match the pinned official GA II LCD 1.6 firmware')
    if VERSION.encode() not in data:
        raise UpdateError('Firmware model/version marker is missing')
    return data


def packets(command, data):
    """Report B: total length BE32, zero-based index BE24, payload length BE16."""
    for number, offset in enumerate(range(0, max(1, len(data)), 1013)):
        chunk = data[offset:offset + 1013]
        header = bytes([2, command]) + len(data).to_bytes(4, 'big')
        header += number.to_bytes(3, 'big') + len(chunk).to_bytes(2, 'big')
        yield (header + chunk).ljust(1024, b'\0')


def write(device, packet):
    if device.write(packet) != len(packet):
        raise UpdateError('Short USB write; stopped without retry')


def drain(device):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not device.read(512, 300):
            return
    raise UpdateError('Device input is continuously busy; close other cooler software')


def read_version(device, timeout=8):
    drain(device)
    write(device, bytes([1, 0x86]) + bytes(62))
    parts = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reply = bytes(device.read(512, 250))
        if not reply:
            continue
        if len(reply) < 6 or reply[:2] != b'\x01\x86':
            continue
        index, length = int.from_bytes(reply[3:5], 'big'), reply[5]
        if index not in (0, 1) or length > 58 or len(reply) < 6 + length:
            raise UpdateError('Malformed firmware-version response')
        parts[index] = reply[6:6 + length].rstrip(b'\0').decode('ascii')
        if len(parts) == 2:
            return parts[0], parts[1]
    raise UpdateError('Timed out reading both firmware-version reports')


def validate_device_version(version):
    if not version.startswith('N9,01,HS,SQ,CA_II-Vision,'):
        raise UpdateError('Cooler chip/boot/model identity does not match the pinned firmware')


def transfer(device, image, log, progress=lambda value: None):
    """Vendor sequence, with bounded reads and no automatic retry or reset."""
    validate_image(image)
    drain(device)
    count = (len(image) + 1012) // 1013
    deadline = time.monotonic() + 180
    for number, packet in enumerate(packets(11, image)):
        if time.monotonic() >= deadline:
            raise UpdateError('Upload exceeded 180 seconds; activation was not sent')
        write(device, packet)
        reply = bytes(device.read(512, 10000))
        if not reply:
            raise UpdateError(f'No response to chunk {number}; activation was not sent')
        if reply != packet[:512]:
            raise UpdateError(f'Unexpected response to chunk {number}: {reply.hex()}; activation was not sent')
        # All 619 replies in the verified 1.3 -> 1.6 hardware update echoed the
        # first 512 output bytes. Check index, size and echoed data so stale
        # replies cannot advance the upload. This is not flash readback.
        log('chunk', number=number, response=reply.hex())
        progress((number + 1) * 100 // count)
        time.sleep(.01)
    log('uploaded', chunks=count)
    time.sleep(2)
    log('activating')
    write(device, next(packets(10, b'\x01')))
    # The activation reply may be lost when USB disconnects. Re-enumeration and
    # a fresh version query below are required; a successful write is not proof.


@contextmanager
def open_device(hid, entry):
    device = hid.device()
    try:
        device.open_path(entry['path'])
        yield device
    finally:
        device.close()


def select_device(hid, serial=None):
    entries = [e for e in hid.enumerate(VID, PID)
               if e.get('interface_number') == 1 and (serial is None or e.get('serial_number') == serial)]
    if len(entries) != 1:
        raise UpdateError(f'Expected exactly one matching GA II LCD interface, found {len(entries)}')
    return entries[0]


def verify_reboot(hid, original, timeout=35):
    deadline = time.monotonic() + timeout
    last_error = 'device did not return'
    while time.monotonic() < deadline:
        time.sleep(1)
        try:
            entry = select_device(hid, original['serial_number'])
            # hidapi-libusb paths encode the physical USB port/interface.
            if entry['path'] != original['path']:
                raise UpdateError('Cooler returned on a different USB path')
            with open_device(hid, entry) as device:
                version, date = read_version(device, timeout=3)
            if version == VERSION:
                return version, date
            last_error = f'cooler reports {version!r}'
        except (OSError, UpdateError) as exc:
            last_error = str(exc)
    raise UpdateError('Post-update verification failed: ' + last_error)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Linux GA II LCD firmware updater (0416:7395 only)')
    parser.add_argument('--image', type=Path, help='official 1.6 image; downloaded if omitted')
    parser.add_argument('--serial', help='select a specific cooler')
    parser.add_argument('--flash', action='store_true', help='upload, activate and verify (default: inspect only)')
    args = parser.parse_args(argv)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'ga2-firmware.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError('Another firmware updater is running')
        if args.image:
            image = args.image.read_bytes()
        else:
            print('Downloading official firmware…', flush=True)
            with urllib.request.urlopen(URL, timeout=30) as response:
                image = response.read(SIZE + 1)
        validate_image(image)
        import hid
        entry = select_device(hid, args.serial)
        if not entry.get('serial_number'):
            raise UpdateError('Cooler has no serial number; cannot verify its identity after reboot')
        with open_device(hid, entry) as device:
            version, date = read_version(device)
        validate_device_version(version)
        print(f"Cooler: {entry['product_string']} / {entry['serial_number']}")
        print(f'Installed: {version} ({date})')
        print(f'Target: {VERSION}; SHA256 verified; {(SIZE + 1012) // 1013} chunks')
        if version == VERSION:
            print('Already up to date. No firmware written.')
            return 0
        # Refuse downgrades or unknown revisions; support only the observed 1.3
        # and earlier vendor 1.4/1.5 releases with this same boot identity.
        if version.split(',')[-1] not in ('1.3', '1.4', '1.5'):
            raise UpdateError('Unsupported installed revision; refusing a possible downgrade')
        if not args.flash:
            print('Inspection passed. No firmware written. Use --flash to update.')
            return 0
        journal = STATE / ('ga2-firmware-' + time.strftime('%Y%m%d-%H%M%S') + '.jsonl')
        with journal.open('x') as record:
            os.chmod(journal, 0o600)
            def log(event, **fields):
                record.write(json.dumps(dict(time=time.time(), event=event, **fields)) + '\n')
                record.flush()
                os.fsync(record.fileno())
            log('start', serial=entry['serial_number'], path=os.fsdecode(entry['path']),
                installed=version, target=VERSION, sha256=SHA256)
            print(f'Updating; journal: {journal}', flush=True)
            last_percent = [-1]
            def progress(percent):
                if percent // 5 != last_percent[0] // 5:
                    print(f'Upload {percent}%', flush=True)
                    last_percent[0] = percent
            try:
                with open_device(hid, entry) as device:
                    current, _ = read_version(device)
                    if current != version:
                        raise UpdateError('Device firmware changed since preflight')
                    transfer(device, image, log, progress)
                result, build = verify_reboot(hid, entry)
                log('verified', version=result, build=build)
            except BaseException as exc:
                log('failed', error=str(exc))
                raise
        print(f'Update verified: {result} ({build})')
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (UpdateError, OSError, ValueError) as exc:
        print(f'Update stopped: {exc}', file=sys.stderr)
        sys.exit(1)
