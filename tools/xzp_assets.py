"""Inspect Crackdown TU0 XUIZ v1 packages and build separate PNG replacement copies.

No Xbox SDK or third-party packages required. Sources are opened read-only;
outputs must be new files/directories. This does not install or activate mods.
"""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PureWindowsPath
import struct
import sys
import zlib

HEADER = struct.Struct('>4sIIIIH')
ENTRY = struct.Struct('>IIB')
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
MAX_BYTES = 256 * 1024 * 1024
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + number for prefix in ('COM', 'LPT') for number in '123456789\u00b9\u00b2\u00b3'}


@dataclass(frozen=True)
class Resource:
    name: str
    data: bytes


def safe_parts(name):
    """Allow only portable relative file names, including on Windows hosts."""
    path = PureWindowsPath(name)
    parts = name.replace('\\', '/').split('/')
    if (path.drive or path.root or not name or
            any(p in ('', '.', '..') or any(c in ':<>"|?*' or ord(c) < 32 for c in p) or
                p.endswith(('.', ' ')) or p.split('.')[0].upper() in RESERVED
                for p in parts)):
        raise ValueError('Unsafe resource name: ' + repr(name))
    return parts


def parse(data):
    if len(data) < HEADER.size or len(data) > MAX_BYTES:
        raise ValueError('Package size is outside supported bounds')
    magic, version, size, flags, table_size, count = HEADER.unpack_from(data)
    if (magic, version, flags) != (b'XUIZ', 1, 0):
        raise ValueError('Only uncompressed XUIZ version 1 packages are supported')
    if size != len(data):
        raise ValueError('Package length does not match header')
    payload_start = HEADER.size + table_size
    if payload_start > len(data):
        raise ValueError('Table exceeds package size')
    pos = HEADER.size
    resources, extent = [], 0
    for _ in range(count):
        if pos + ENTRY.size > payload_start:
            raise ValueError('Truncated resource entry')
        length, offset, name_units = ENTRY.unpack_from(data, pos)
        pos += ENTRY.size
        if pos + name_units * 2 > payload_start:
            raise ValueError('Truncated resource name')
        name = data[pos:pos + name_units * 2].decode('utf-16-be')
        pos += name_units * 2
        safe_parts(name)
        # Two original map packages contain duplicate names. Preserve them
        # verbatim for listing/copying; reject ambiguous extraction/replacement.
        # TU0 packages have contiguous payloads in table order. Refuse other
        # layouts rather than silently discarding unknown bytes on repack.
        if offset != extent or payload_start + offset + length > len(data):
            raise ValueError('Unsupported resource extent: ' + name)
        resources.append(Resource(name, data[payload_start + offset:payload_start + offset + length]))
        extent += length
    if pos != payload_start or payload_start + extent != len(data):
        raise ValueError('Unexpected table or payload bytes')
    return resources


def read_package(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_BYTES + 1)
    return parse(data)


def pack(resources):
    table, payload, offset = bytearray(), bytearray(), 0
    for resource in resources:
        safe_parts(resource.name)
        name = resource.name.encode('utf-16-be')
        if len(name) // 2 > 255:
            raise ValueError('Resource name exceeds format limit')
        table += ENTRY.pack(len(resource.data), offset, len(name) // 2) + name
        payload += resource.data
        offset += len(resource.data)
    if len(resources) > 65535 or HEADER.size + len(table) + len(payload) > MAX_BYTES:
        raise ValueError('Package exceeds supported bounds')
    data = HEADER.pack(b'XUIZ', 1, HEADER.size + len(table) + len(payload),
                       0, len(table), len(resources)) + table + payload
    parse(data)
    return data


def png_dimensions(data, validate=False):
    if (len(data) < 33 or data[:8] != PNG_MAGIC or
            data[8:16] != b'\x00\x00\x00\rIHDR'):
        raise ValueError('Expected a PNG with an IHDR header')
    width, height = struct.unpack_from('>II', data, 16)
    if not width or not height or width > 16384 or height > 16384:
        raise ValueError('PNG dimensions outside supported bounds')
    if validate:
        pos, ended, saw_data = 8, False, False
        while pos < len(data):
            if pos + 12 > len(data):
                raise ValueError('Truncated PNG chunk')
            length = struct.unpack_from('>I', data, pos)[0]
            end = pos + 12 + length
            if end > len(data):
                raise ValueError('PNG chunk exceeds file size')
            kind = data[pos + 4:pos + 8]
            stored_crc = struct.unpack_from('>I', data, end - 4)[0]
            if zlib.crc32(data[pos + 4:end - 4]) != stored_crc:
                raise ValueError('PNG chunk CRC mismatch')
            if kind == b'IDAT':
                saw_data = True
            if kind == b'IEND':
                if length or end != len(data):
                    raise ValueError('Invalid PNG end chunk')
                ended = True
            pos = end
        if not ended or not saw_data:
            raise ValueError('PNG is missing image data or end chunk')
    return width, height


def replace_png(resources, entry_name, replacement, allow_resize=False):
    key = entry_name.replace('\\', '/').casefold()
    matches = [i for i, r in enumerate(resources) if r.name.replace('\\', '/').casefold() == key]
    if len(matches) != 1 or resources[matches[0]].name != entry_name:
        raise ValueError('Expected one exact entry name; use list to inspect names')
    index = matches[0]
    original = resources[index]
    if PureWindowsPath(original.name).suffix.lower() != '.png':
        raise ValueError('Replacement supports PNG entries only')
    original_size = png_dimensions(original.data)
    new_size = png_dimensions(replacement, validate=True)
    if new_size != original_size and not allow_resize:
        raise ValueError('PNG dimensions changed; --allow-resize requires separate UI layout validation')
    updated = list(resources)
    updated[index] = Resource(original.name, replacement)
    return updated


def write_new(path, data):
    path = Path(path)
    if path.is_symlink():
        raise FileExistsError('Output is an existing symbolic link: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as destination:
        destination.write(data)


def extract(resources, directory, entry_name=None):
    selected = resources if entry_name is None else [r for r in resources if r.name == entry_name]
    if not selected:
        raise ValueError('No matching entry; use exact name from list')
    # Require a new directory, so no existing source, hard link, or symlink
    # can be overwritten. Validate every name before creating that directory.
    paths = [(r, safe_parts(r.name)) for r in selected]
    seen = set()
    for resource, parts in paths:
        key = '/'.join(parts).casefold()
        if key in seen or any(key.startswith(k + '/') or k.startswith(key + '/') for k in seen):
            raise ValueError('Conflicting extraction path: ' + resource.name)
        seen.add(key)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    for resource, parts in paths:
        write_new(root.joinpath(*parts), resource.data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='List resources and PNG dimensions')
    listing.add_argument('package', type=Path)
    extracting = commands.add_parser('extract', help='Extract into a new directory')
    extracting.add_argument('package', type=Path)
    extracting.add_argument('--output', required=True, type=Path)
    extracting.add_argument('--entry', help='Exact resource name; otherwise extract all')
    copying = commands.add_parser('copy', help='Repack unchanged resources to a new file')
    copying.add_argument('package', type=Path)
    copying.add_argument('--output', required=True, type=Path)
    replacing = commands.add_parser('replace', help='Replace one PNG in a new package copy')
    replacing.add_argument('package', type=Path)
    replacing.add_argument('--entry', required=True)
    replacing.add_argument('--png', required=True, type=Path)
    replacing.add_argument('--output', required=True, type=Path)
    replacing.add_argument('--allow-resize', action='store_true')
    args = parser.parse_args(argv)
    try:
        resources = read_package(args.package)
        if args.command == 'list':
            result = []
            for resource in resources:
                row = {'name': resource.name, 'bytes': len(resource.data)}
                if resource.data.startswith(PNG_MAGIC):
                    row['width'], row['height'] = png_dimensions(resource.data)
                result.append(row)
            print(json.dumps(result, indent=2))
        elif args.command == 'extract':
            extract(resources, args.output, args.entry)
            print('Extracted to ' + str(args.output))
        else:
            if args.command == 'replace':
                with args.png.open('rb') as source:
                    replacement = source.read(MAX_BYTES + 1)
                if len(replacement) > MAX_BYTES:
                    raise ValueError('Replacement is too large')
                resources = replace_png(resources, args.entry, replacement, args.allow_resize)
            write_new(args.output, pack(resources))
            print('Wrote separate package ' + str(args.output))
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, 'Error: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
