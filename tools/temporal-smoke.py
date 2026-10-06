"""Bounded temporal-renderer smoke capture using a separate automation session."""
import argparse
import ctypes as C
import csv
import json
import math
import re
import struct
import time
from pathlib import Path
from benchmark_windows import FrameProbe
from control_game import Controller, BUTTONS


class MemoryCounters(C.Structure):
    _fields_ = [('cb', C.c_uint32), ('page_faults', C.c_uint32)] + [
        (name, C.c_size_t) for name in ('peak_working_set', 'working_set',
        'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile',
        'peak_pagefile', 'private')]


def camera_orientation(probe):
    manager = struct.unpack('>I', probe.read_guest(0x82DE2574, 4))[0]
    camera = struct.unpack('>I', probe.read_guest(manager + 272, 4))[0] if manager else 0
    if not camera or struct.unpack('>I', probe.read_guest(camera + 6500, 4))[0] != 3:
        raise RuntimeError('A normal gameplay camera is required for the pan check')
    # TU0 copies these three orientation rows into its render snapshot.
    # Read only; do not confuse controller acknowledgement with camera response.
    packed = struct.unpack('>12f', probe.read_guest(camera + 272, 48))
    # These are three XYZ rows with unused fourth lanes. The unused lanes can
    # contain changing scratch data and must never count as camera movement.
    rows = tuple(packed[index] for index in (0, 1, 2, 4, 5, 6, 8, 9, 10))
    if not all(math.isfinite(value) for value in rows):
        raise RuntimeError('Invalid gameplay camera orientation')
    return camera, rows


def pan_check(controller, probe, directory, dismiss_intel):
    deadline = time.monotonic() + (35 if dismiss_intel else 1)
    attempts = 0
    while True:
        attempts += 1
        camera, before = camera_orientation(probe)
        sequence = controller.state(rx=28000, lease_ms=3000)
        try:
            time.sleep(1)
            polled = controller.read_int(40) == sequence
            after_camera, after = camera_orientation(probe)
            delta = max(abs(a - b) for a, b in zip(before, after))
            if polled and camera == after_camera and delta > 1e-4:
                capture = str(controller.screenshot(directory / 'automation'))
                return capture, dict(attempts=attempts, orientation_delta=delta,
                                     controller_polled=True)
        finally:
            controller.state(lease_ms=0)
        if not dismiss_intel or time.monotonic() >= deadline:
            raise RuntimeError('Camera did not respond to input; dismiss menus/intel before sampling')
        # Initial intel playback can ignore Back until its clip finishes. Try a
        # bounded dismissal, then recheck actual camera response before soaking.
        controller.press(BUTTONS['b'], .15)
        time.sleep(1)


def soak(probe, directory, duration, max_growth_mib):
    psapi = C.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32]
    rows = []
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        counters = MemoryCounters()
        counters.cb = C.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(probe.handle, C.byref(counters), counters.cb):
            raise C.WinError(C.get_last_error())
        frame, completed = probe.read()
        rows.append(dict(seconds=elapsed, frame=frame, completed=completed,
                         private_bytes=counters.private, working_set=counters.working_set))
        if len(rows) > 5 and frame == rows[-5]['frame']:
            raise RuntimeError('Frame counter stalled during temporal soak')
        if completed > frame:
            raise RuntimeError('Invalid completed-frame counter during temporal soak')
        if elapsed >= duration:
            break
        time.sleep(min(.5, duration - elapsed))
    with (directory / 'soak.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    growth = max(row['private_bytes'] for row in rows) - rows[0]['private_bytes']
    if growth > max_growth_mib * 1024**2:
        raise RuntimeError(f'Temporal private memory grew {growth / 1024**2:.1f} MiB; limit {max_growth_mib}')
    failures = re.compile(r'Failed to create a D3D upload buffer|Temporal constant upload failed|'
                          r'Temporal device failure|PM4_DRAW.*Failed in backend')
    for log in directory.glob('game*.log'):
        with log.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if failures.search(line):
                    raise RuntimeError(f'Renderer error in {log.name}: {line.strip()}')
    return dict(duration=rows[-1]['seconds'], frames=rows[-1]['frame'] - rows[0]['frame'],
                private_start=rows[0]['private_bytes'], private_max=max(row['private_bytes'] for row in rows),
                private_growth_mib=growth / 1024**2, samples=len(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True, help='JSON emitted by launch-temporal.ps1')
    parser.add_argument('--metadata', type=Path, default=Path('out/variants/renderer-temporal/offsets.json'))
    parser.add_argument('--load', action='store_true', help='Navigate default campaign choices on this private profile')
    parser.add_argument('--pan', action='store_true')
    parser.add_argument('--soak', type=float, default=0, metavar='SECONDS', help='Observe frame progress and bounded private memory, up to 600 seconds')
    parser.add_argument('--max-growth-mib', type=float, default=512)
    args = parser.parse_args()
    if not 0 <= args.soak <= 600 or args.max_growth_mib <= 0:
        parser.error('Soak must be between 0 and 600 seconds, with a positive memory growth limit')
    if not args.metadata.is_file():
        parser.error('Metadata missing; stage the renderer with tools/build-renderer.ps1 -Variant temporal')
    session = json.loads(args.session.read_text(encoding='utf-8-sig'))
    pid = session['GameProcessId']
    directory = Path(session['RunDirectory'])
    controller = None
    probe = None
    captures = []
    try:
        # The launcher returns before the runtime publishes its graphics pointer.
        deadline = time.monotonic() + 30
        while True:
            try:
                probe = FrameProbe(pid, args.metadata)
                break
            except (OSError, RuntimeError) as error:
                if isinstance(error, RuntimeError) and 'not ready' not in str(error):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.2)
        controller = Controller(pid)
        if args.load:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    manager = struct.unpack('>I', probe.read_guest(0x82DE2574, 4))[0]
                except OSError:
                    # Guest executable pages are populated after graphics startup.
                    time.sleep(.2)
                    continue
                camera = struct.unpack('>I', probe.read_guest(manager + 272, 4))[0] if manager else 0
                if camera:
                    position = struct.unpack('>3f', probe.read_guest(camera + 320, 12))
                    mode = struct.unpack('>I', probe.read_guest(camera + 6500, 4))[0]
                    if mode == 3 and any(abs(v) > 1 for v in position):
                        break
                controller.press(BUTTONS['start'], .15)
                time.sleep(.5)
                controller.press(BUTTONS['a'], .15)
                time.sleep(2)
            else:
                raise TimeoutError('Default campaign navigation did not reach a normal camera')
            # Allow loading/intro to finish, then dismiss the initial intel panel.
            time.sleep(4)
            controller.press(BUTTONS['b'], .15)
        pan = None
        if args.pan or args.load:
            capture, pan = pan_check(controller, probe, directory, args.load)
            captures.append(capture)
            controller.press(0, .5, rx=-28000)
        for _ in range(2):
            captures.append(str(controller.screenshot(directory / 'automation')))
            time.sleep(.5)
        result = {'pid': pid, 'mode': session['Mode'], 'captures': captures, 'identity': probe.identity}
        if pan:
            result['camera_pan'] = pan
        if args.soak:
            result['soak'] = soak(probe, directory, args.soak, args.max_growth_mib)
            result['captures'].append(str(controller.screenshot(directory / 'automation')))
        (directory / 'smoke.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        if controller:
            controller.state(lease_ms=0)
            controller.close()
        if probe:
            probe.close()


if __name__ == '__main__':
    main()
