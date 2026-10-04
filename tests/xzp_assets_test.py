"""Validate package extents, replacement preservation, and safe output behavior."""
from pathlib import Path
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from xzp_assets import (HEADER, Resource, extract, pack, parse, PNG_MAGIC,
                        png_dimensions, replace_png, safe_parts, write_new)


def png(width=1, height=1, rgb=b'\x00\x00\x00'):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (PNG_MAGIC + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress((b'\x00' + rgb * width) * height)) + chunk(b'IEND', b''))


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.resources = [Resource('menu.xur', b'XUIB\x00\x01'), Resource('icons\\a.png', png())]

    def test_roundtrip_and_variable_size_replacement_preserve_other_resources(self):
        original = pack(self.resources)
        self.assertEqual(pack(parse(original)), original)
        replacement = png(rgb=b'\xff\x00\x00')
        updated = replace_png(parse(original), 'icons\\a.png', replacement)
        reparsed = parse(pack(updated))
        self.assertEqual(reparsed[0], self.resources[0])
        self.assertEqual(reparsed[1].data, replacement)
        self.assertEqual(self.resources[1].data, png())

    def test_corrupt_extents_lengths_and_versions_rejected(self):
        original = pack(self.resources)
        changes = [(4, 2), (8, len(original) - 1), (12, 1), (16, 0),
                   (HEADER.size + 4, 1), (HEADER.size, 0xffffffff)]
        for offset, value in changes:
            altered = bytearray(original)
            struct.pack_into('>I', altered, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse(altered)
        with self.assertRaises(ValueError):
            parse(original[:-1])
        with self.assertRaises(ValueError):
            parse(original + b'junk')

    def test_traversal_drives_and_windows_alias_names_rejected(self):
        names = ['..\\bad.png', '/bad', 'C:\\bad', 'a/../b', 'a//b', 'a:b',
                 'NUL.png', 'a/CON', 'a.', 'a ', '\\\\server\\file']
        for name in names:
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_parts(name)
        self.assertEqual(safe_parts('xui assets\\start.png'), ['xui assets', 'start.png'])

    def test_duplicate_windows_names_preserved_but_extraction_rejected(self):
        resources = [Resource('icons\\a.png', png()), Resource('ICONS/A.PNG', png())]
        self.assertEqual(parse(pack(resources)), resources)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'new'
            with self.assertRaises(ValueError):
                extract(resources, target)
            self.assertFalse(target.exists())
        exact_duplicates = [resources[0], resources[0]]
        with self.assertRaises(ValueError):
            replace_png(exact_duplicates, 'icons\\a.png', png())
        with self.assertRaises(ValueError):
            replace_png(resources, 'icons\\a.png', png())

    def test_png_validation_and_resize_opt_in(self):
        with self.assertRaises(ValueError):
            replace_png(self.resources, 'icons\\a.png', png(2, 1))
        updated = replace_png(self.resources, 'icons\\a.png', png(2, 1), True)
        self.assertEqual(png_dimensions(updated[1].data), (2, 1))
        invalid = bytearray(png())
        invalid[-1] ^= 1
        with self.assertRaises(ValueError):
            replace_png(self.resources, 'icons\\a.png', invalid)
        with self.assertRaises(ValueError):
            replace_png(self.resources, 'menu.xur', png())
        with self.assertRaises(ValueError):
            replace_png(self.resources, 'missing.png', png())

    def test_existing_files_directories_and_hard_links_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.xzp'
            write_new(source, b'original')
            with self.assertRaises(FileExistsError):
                write_new(source, b'changed')
            with self.assertRaises(FileExistsError):
                extract(self.resources, root)
            self.assertEqual(source.read_bytes(), b'original')
            alias = root / 'alias.xzp'
            os.link(source, alias)
            with self.assertRaises(FileExistsError):
                write_new(alias, b'changed')
            self.assertEqual(source.read_bytes(), b'original')
            extract(self.resources, root / 'new', 'icons\\a.png')
            self.assertEqual((root / 'new/icons/a.png').read_bytes(), png())
            self.assertFalse((root / 'new/menu.xur').exists())


if __name__ == '__main__':
    unittest.main()
