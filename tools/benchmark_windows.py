"""Windows adapters for physical controller recording and read-only frame sampling."""
import ctypes as C
import hashlib
import json
import os
import struct
from pathlib import Path

PAD_FIELDS = ('buttons', 'lt', 'rt', 'lx', 'ly', 'rx', 'ry')


def require_windows():
    if os.name != 'nt':
        raise RuntimeError('Game benchmarking requires Windows')


class TimerPrecision:
    def __enter__(self):
        require_windows()
        self.dll = C.WinDLL('winmm')
        self.active = self.dll.timeBeginPeriod(1) == 0
        return self

    def __exit__(self, *args):
        if self.active:
            self.dll.timeEndPeriod(1)


class XInput:
    class State(C.Structure):
        _fields_ = [('packet', C.c_uint32), ('buttons', C.c_uint16),
                    ('lt', C.c_uint8), ('rt', C.c_uint8), ('lx', C.c_int16),
                    ('ly', C.c_int16), ('rx', C.c_int16), ('ry', C.c_int16)]

    def __init__(self, index):
        require_windows()
        self.dll = C.WinDLL('xinput1_4')
        self.dll.XInputGetState.argtypes = [C.c_uint32, C.POINTER(self.State)]
        self.dll.XInputGetState.restype = C.c_uint32
        self.index = index
        self.read()  # Fail before starting a recording if disconnected.

    def read(self):
        state = self.State()
        error = self.dll.XInputGetState(self.index, C.byref(state))
        if error:
            raise RuntimeError(f'XInput controller {self.index} disconnected (error {error})')
        return {name: getattr(state, name) for name in PAD_FIELDS}


def export_rva(path, symbol):
    """Read a PE export without loading or executing the DLL in this process."""
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise RuntimeError('Invalid PE image')
    sections, optional_size = struct.unpack_from('<H12xH', data, pe + 6)
    optional = pe + 24
    if struct.unpack_from('<H', data, optional)[0] != 0x20b:
        raise RuntimeError('Expected a 64-bit runtime DLL')
    directory = struct.unpack_from('<I', data, optional + 112)[0]
    table = optional + optional_size

    def offset(rva):
        for i in range(sections):
            size, address, raw_size, raw = struct.unpack_from('<IIII', data, table + i * 40 + 8)
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise RuntimeError(f'Unmapped PE RVA {rva:x}')

    exports = offset(directory)
    count, functions, names, ordinals = struct.unpack_from('<IIII', data, exports + 24)
    for i in range(count):
        name = offset(struct.unpack_from('<I', data, offset(names) + i * 4)[0])
        end = data.index(b'\0', name)
        if data[name:end].decode('ascii') == symbol:
            ordinal = struct.unpack_from('<H', data, offset(ordinals) + i * 2)[0]
            return struct.unpack_from('<I', data, offset(functions) + ordinal * 4)[0]
    raise RuntimeError(f'Runtime export unavailable: {symbol}')


class FrameProbe:
    """Poll D3D12 guest frame submissions; never write target process memory.

    Private member offsets are accepted only for the exact SDK runtime/plugin
    hashes prepared from their matching headers. No injection or debugger attach.
    """
    def __init__(self, pid, metadata_path):
        require_windows()
        metadata = json.loads(Path(metadata_path).read_text(encoding='utf-8-sig'))
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
        self.kernel.OpenProcess.restype = C.c_void_p
        self.kernel.ReadProcessMemory.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p,
                                                C.c_size_t, C.c_void_p]
        self.kernel.CloseHandle.argtypes = [C.c_void_p]
        self.kernel.GetProcessTimes.argtypes = [C.c_void_p] + [C.c_void_p] * 4
        self.handle = self.kernel.OpenProcess(0x410, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            psapi = C.WinDLL('psapi', use_last_error=True)
            psapi.EnumProcessModules.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p]
            psapi.GetModuleFileNameExW.argtypes = [C.c_void_p, C.c_void_p, C.c_wchar_p, C.c_uint32]
            modules = (C.c_void_p * 1024)()
            needed = C.c_uint32()
            if not psapi.EnumProcessModules(self.handle, modules, C.sizeof(modules), C.byref(needed)):
                raise C.WinError(C.get_last_error())
            found = {}
            for base in modules[:min(needed.value // C.sizeof(C.c_void_p), 1024)]:
                path = C.create_unicode_buffer(32768)
                if not psapi.GetModuleFileNameExW(self.handle, base, path, len(path)):
                    raise C.WinError(C.get_last_error())
                found[Path(path.value).name.lower()] = (base, Path(path.value))
            for dll, expected in metadata['dll_sha256'].items():
                if dll not in found:
                    raise RuntimeError(f'{dll} not loaded; frame sampling requires the Xenos/D3D12 backend')
                actual = hashlib.sha256(found[dll][1].read_bytes()).hexdigest()
                if actual != expected:
                    raise RuntimeError(f'{dll} differs from prepared SDK; rerun prepare-benchmark.ps1 with its matching SDK')
            base, path = found['rexruntime.dll']
            runtime = self.read64(base + export_rva(path, '?instance_@Runtime@rex@@0PEAV12@EA'))
            graphics = self.read64(runtime + metadata['runtime_graphics'])
            cp = self.read64(graphics + metadata['graphics_cp'])
            if not runtime or not graphics or not cp:
                raise RuntimeError('Runtime graphics not ready; load into gameplay first')
            # Both D3D12 and Vulkan live in the Xenos DLL. Reject the latter
            # before interpreting any D3D12-specific member offsets.
            vtable = self.read64(cp)
            locator = self.read64(vtable - 8)
            info = self.read_bytes(locator, 24)
            signature, _, _, type_rva, _, self_rva = struct.unpack('<6I', info)
            if signature != 1:
                raise RuntimeError('Unsupported command processor RTTI')
            type_name = self.read_bytes(locator - self_rva + type_rva + 16, 96).split(b'\0', 1)[0]
            if type_name != b'.?AVD3D12CommandProcessor@d3d12@graphics@rex@@':
                raise RuntimeError('Frame sampling requires the D3D12 command processor')
            self.frame_address = cp + metadata['cp_frame']
            self.completed_address = cp + metadata['cp_completed']
            self.virtual_membase = None
            if 'runtime_memory' in metadata and 'memory_virtual' in metadata:
                memory = self.read64(runtime + metadata['runtime_memory'])
                self.virtual_membase = self.read64(memory + metadata['memory_virtual'])
            self.identity = {'sdk': metadata['sdk'], 'dll_sha256': metadata['dll_sha256']}
            self.read()
        except BaseException:
            self.close()
            raise

    def read64(self, address):
        return struct.unpack('<Q', self.read_bytes(address, 8))[0]

    def read_bytes(self, address, size):
        value = C.create_string_buffer(size)
        if not self.kernel.ReadProcessMemory(self.handle, address, value, size, None):
            raise C.WinError(C.get_last_error())
        return value.raw

    def read(self):
        return self.read64(self.frame_address), self.read64(self.completed_address)

    def read_guest(self, address, size):
        if self.virtual_membase is None:
            raise RuntimeError('Guest inspection needs updated metadata; run prepare-benchmark.ps1')
        if not 0 <= address <= 0xffffffff or not 0 < size <= 0x10000000 or address + size > 0x100000000:
            raise ValueError('Guest read must stay within the 32-bit address space (maximum 256 MiB)')
        return self.read_bytes(self.virtual_membase + address, size)

    def cpu_seconds(self):
        values = [C.c_uint64() for _ in range(4)]
        if not self.kernel.GetProcessTimes(self.handle, *(C.byref(v) for v in values)):
            raise C.WinError(C.get_last_error())
        return (values[2].value + values[3].value) / 10_000_000

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
