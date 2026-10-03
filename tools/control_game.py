"""Send Xbox controller input and request guest-only screenshots (--automation=true)."""
import argparse
import ctypes
import os
import struct
import time
from pathlib import Path

BUTTONS = dict(up=1, down=2, left=4, right=8, start=16, back=32,
               ls=64, rs=128, lb=256, rb=512, a=4096, b=8192, x=16384, y=32768)


class Controller:
    def __init__(self, pid):
        if os.name != 'nt':
            raise RuntimeError('This interface requires Windows')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        self.kernel.OpenFileMappingW.restype = ctypes.c_void_p
        self.kernel.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        self.kernel.MapViewOfFile.restype = ctypes.c_void_p
        self.kernel.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel.OpenFileMappingW(0xF001F, False, f'Local\\CrackdownAutomation-{pid}')
        if not self.handle:
            raise RuntimeError(f'No automation interface for PID {pid}; launch with --automation=true')
        self.address = self.kernel.MapViewOfFile(self.handle, 0xF001F, 0, 0, 4096)
        if not self.address:
            self.kernel.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())
        if struct.unpack('<II', ctypes.string_at(self.address, 8)) != (0x43444131, 1):
            self.close()
            raise RuntimeError('Unsupported automation protocol')
        self.pid = pid

    def close(self):
        if self.address:
            self.kernel.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def read_int(self, offset):
        return struct.unpack('<I', ctypes.string_at(self.address + offset, 4))[0]

    def exchange(self, offset, value):
        # Aligned 32-bit stores are atomic on Windows x64. The seqlock has one
        # external writer, and x64 preserves the order of these stores.
        ctypes.c_uint32.from_address(self.address + offset).value = value & 0xFFFFFFFF

    def state(self, buttons=0, lt=0, rt=0, lx=0, ly=0, rx=0, ry=0, lease_ms=1000):
        sequence = (self.read_int(8) + 2) & 0xFFFFFFFE
        self.exchange(8, sequence | 1)
        expires = (self.kernel.GetTickCount() + lease_ms) & 0xFFFFFFFF
        payload = struct.pack('<HBBhhhhI', buttons, lt, rt, lx, ly, rx, ry, expires)
        ctypes.memmove(self.address + 12, payload, len(payload))
        self.exchange(8, sequence)
        return sequence

    def press(self, buttons, seconds=0.2, **axes):
        # Refresh the lease during long holds, and release even after an error.
        deadline = time.monotonic() + seconds
        acknowledged = False
        try:
            while time.monotonic() < deadline:
                sequence = self.state(buttons=buttons, **axes)
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                acknowledged |= self.read_int(40) == sequence
        finally:
            self.state(lease_ms=0)
        return acknowledged

    def screenshot(self, directory, timeout=10):
        sequence = (self.read_int(28) + 1) & 0x7FFFFFFF
        self.exchange(28, sequence)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.read_int(32) == sequence:
                if self.read_int(36) != 1:
                    raise RuntimeError('Frame capture failed; the game may not have presented yet')
                ppm = Path(directory) / str(self.pid) / f'frame-{sequence}.ppm'
                # Convert the SDK RGB dump to PNG for convenient viewing.
                from PIL import Image
                png = ppm.with_suffix('.png')
                with Image.open(ppm) as image:
                    image.save(png)
                return png.resolve()
            time.sleep(0.02)
        raise TimeoutError('Frame capture did not complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--press', nargs='+', choices=BUTTONS)
    parser.add_argument('--seconds', type=float, default=0.2)
    parser.add_argument('--lt', type=int, default=0)
    parser.add_argument('--rt', type=int, default=0)
    for axis in ('lx', 'ly', 'rx', 'ry'):
        parser.add_argument(f'--{axis}', type=int, default=0)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parents[1] / 'out' / 'automation')
    args = parser.parse_args()
    if not 0 < args.seconds <= 60:
        parser.error('--seconds must be greater than zero and at most 60')
    if any(not -32768 <= getattr(args, a) <= 32767 for a in ('lx', 'ly', 'rx', 'ry')):
        parser.error('Stick axes must be in -32768..32767')
    if any(not 0 <= getattr(args, a) <= 255 for a in ('lt', 'rt')):
        parser.error('Triggers must be in 0..255')
    controller = Controller(args.pid)
    try:
        axes = {a: getattr(args, a) for a in ('lt', 'rt', 'lx', 'ly', 'rx', 'ry')}
        if args.press or any(axes.values()):
            acknowledged = controller.press(sum(BUTTONS[b] for b in set(args.press or [])), args.seconds, **axes)
            print('Input polled by game' if acknowledged else 'Input sent; no poll acknowledgement observed')
        if args.capture:
            print(controller.screenshot(args.directory))
        if not args.capture and not args.press and not any(axes.values()):
            controller.state(lease_ms=0)
            print('Controller released')
    finally:
        controller.close()


if __name__ == '__main__':
    main()
