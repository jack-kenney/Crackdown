"""Inventory timing candidates in generated PPC and handwritten C++ sources.

Reads a loaded PE image (RVA layout, not the on-disk PE layout). Results are
leads for manual review, never an instruction to replace constants globally.
No game process access, code generation, or source mutation is performed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
VALUES = {f'1/{rate} second': 1 / rate for rate in (15, 30, 60, 120, 144, 240)}
VALUES.update({'50 ms': .05, '100 ms': .1, '16 ms': .016, '33 ms': .033,
               '30 frames/second': 30., '60 frames/second': 60.,
               'milliseconds/second': 1000., '40% gain': .4, '60% retention': .6})
CLOCKS = {0x82D99128: 'published step milliseconds',
          0x82D9912C: 'published total milliseconds',
          0x83071084: 'game elapsed seconds',
          0x8307107C: 'game update serial',
          0x82DE25C0: 'engine pointer (logical frame at +16)'}
FUNC = re.compile(r'^DEFINE_REX_FUNC\(([^)]+)\)')
MEMORY = re.compile(r'(-?\d+)\((r\d+)\)$')
SOURCE_PATTERN = re.compile(
    r'(?:0\.(?:03333|06666|01666)\d*|\b1(?:\.0[fd]?)?\s*/\s*(?:15|30|60|120|144|240)\b'
    r'|\b(?:30|60|120|144|240)\s*\*\s*(?:dt|delta|elapsed)'
    r'|\b(?:dt|delta|elapsed)\w*\s*\*\s*(?:30|60|120|144|240)\b'
    r'|\b1000\s*/\s*(?:15|30|60|120|144|240)\b'
    r'|\bDeltaTime\s*=|\b(?:reference_dt|refresh_rate_hz|vsync_interval_ticks)\b)', re.I)


def loaded_image(path):
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[:2] != b'MZ' or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Expected a loaded Xbox 360 PE image.')
    optional = pe + 24
    if struct.unpack_from('<H', data, optional)[0] != 0x10b:
        raise ValueError('Expected a PE32 guest image.')
    base = struct.unpack_from('<I', data, optional + 28)[0]
    if base != 0x82000000 or len(data) < 0x100000:
        raise ValueError('Expected TU0 image base 0x82000000 in loaded RVA layout.')
    table = optional + struct.unpack_from('<H', data, pe + 20)[0]
    sections = []
    for i in range(struct.unpack_from('<H', data, pe + 6)[0]):
        offset = table + i * 40
        length, rva = struct.unpack_from('<II', data, offset + 8)
        sections.append((base + rva, base + rva + length,
                         data[offset:offset + 8].rstrip(b'\0').decode('ascii')))
    if any(end - base > len(data) for _, end, name in sections if name in ('.rdata', '.data')):
        raise ValueError('Loaded image must include the complete .rdata and .data RVA ranges.')
    return data, base, sections


def functions(path):
    name, instructions = None, []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        match = FUNC.match(line)
        if match:
            if name:
                yield name, instructions
            name, instructions = match[1], []
        elif name and line.strip().startswith('// '):
            text = line.strip()[3:].strip()
            if text and not text.startswith(('---', 'NOTE', 'Unknown')):
                op, _, args = text.partition(' ')
                instructions.append((number, op, args.strip(), text))
        elif name and line.startswith('loc_'):
            instructions.append((number, 'label', '', line.strip()))
    if name:
        yield name, instructions


def address(operand, registers):
    match = MEMORY.fullmatch(operand)
    if not match:
        return None
    offset, reg = match.groups()
    value = registers.get(reg, 0 if reg == 'r0' else None)
    return (value + int(offset)) & 0xffffffff if isinstance(value, int) else None


def scan_function(name, code, path, image, base, sections):
    registers, hits, calls = {}, [], []
    definitions = Counter()
    for _, op, arguments, _ in code:
        dest = arguments.split(',')[0]
        if (re.fullmatch(r'r\d+', dest) and not op.startswith(
                ('st', 'cmp', 'cmpl', 'mt', 'dc', 'ic', 'tw'))
                and not (op == 'ld' and re.search(r'-\d+\(r1\)$', arguments))):
            definitions[dest] += 1
    immutable = {reg for reg, writes in definitions.items() if writes == 1 and int(reg[1:]) >= 14}
    for i, (line, op, arguments, text) in enumerate(code):
        args = arguments.split(',')
        following = [entry[3] for entry in code[i + 1:i + 13] if entry[1] != 'label']
        common = dict(function=name, file=path.as_posix(), line=line)
        if op in ('lfs', 'lfd', 'lwz') and len(args) == 2:
            location = address(args[1], registers)
            if location in CLOCKS:
                hits.append(dict(common, kind='clock_reference', clock=CLOCKS[location],
                                 address=hex(location), instruction=text))
            width = 4 if op == 'lfs' else 8
            section = next((s[2] for s in sections if location is not None and
                            s[0] <= location and location + width <= s[1]), None)
            if (op in ('lfs', 'lfd') and section and section not in ('.text', 'BINK')
                    and 0 <= location - base <= len(image) - width):
                value = struct.unpack_from('>f' if width == 4 else '>d', image, location - base)[0]
                for label, target in VALUES.items():
                    # Some authored literals are rounded decimal forms such as
                    # 0.0333333, rather than the nearest float representation.
                    if abs(value - target) <= abs(target) * 2e-6:
                        accumulates = False
                        # A particularly strong lead: a constant added to a
                        # loaded field and then stored back into that same field.
                        previous = code[max(0, i - 5):i]
                        for _, prev_op, prev_args, _ in previous:
                            if prev_op != 'lfs':
                                continue
                            field_reg, _, field = prev_args.partition(',')
                            for j in range(i + 1, min(i + 5, len(code))):
                                _, next_op, next_args, _ = code[j]
                                if next_op not in ('fadds', 'fadd'):
                                    continue
                                parts = next_args.split(',')
                                if len(parts) == 3 and set(parts[1:]) == {args[0], field_reg}:
                                    accumulates |= any(entry[1] == 'stfs' and
                                                       entry[2] == parts[0] + ',' + field
                                                       for entry in code[j + 1:j + 4])
                        hits.append(dict(common, kind='float_constant', label=label,
                                         address=hex(location), section=section, value=value,
                                         instruction=text, self_accumulator=accumulates,
                                         following=following))
        if op in ('li', 'mulli', 'cmpwi', 'cmplwi') and args[-1] in (
                '15', '16', '30', '33', '60', '120', '144', '240', '1000'):
            hits.append(dict(common, kind='integer_candidate', value=int(args[-1]),
                             instruction=text, following=following[:5]))
        if op == 'lwz' and len(args) == 2 and i + 2 < len(code):
            nxt, store = code[i + 1], code[i + 2]
            if (nxt[1] == 'addi' and nxt[2] in (f'{args[0]},{args[0]},1', f'{args[0]},{args[0]},-1')
                    and store[1] == 'stw' and store[2] == arguments):
                hits.append(dict(common, kind='per_call_counter', instruction=text,
                                 field=args[1], change=int(nxt[2].split(',')[-1])))
        if op in ('bl', 'b') and arguments.startswith('0x'):
            calls.append((name, 'sub_' + arguments[2:].upper()))
        # At joins retain only nonvolatile registers with one definition in the
        # entire function. Other values cannot be assumed valid on every path.
        if op == 'label':
            registers = {reg: value for reg, value in registers.items() if reg in immutable}
        elif op in ('bl', 'bctrl'):
            for reg in ['r0'] + [f'r{n}' for n in range(3, 13)]:
                registers.pop(reg, None)
        elif args and re.fullmatch(r'r\d+', args[0]) and not op.startswith(
                ('st', 'cmp', 'cmpl', 'mt', 'dc', 'ic', 'tw')):
            dest, value = args[0], None
            try:
                if op == 'lis': value = int(args[1]) << 16
                elif op == 'li': value = int(args[1])
                elif op == 'mr': value = registers.get(args[1])
                elif op in ('addi', 'addis'):
                    source = 0 if args[1] == 'r0' else registers.get(args[1])
                    if isinstance(source, int):
                        value = source + (int(args[2]) << (16 if op == 'addis' else 0))
                elif op in ('ori', 'oris') and isinstance(registers.get(args[1]), int):
                    value = registers[args[1]] | (int(args[2]) << (16 if op == 'oris' else 0))
                elif op == 'add' and all(isinstance(registers.get(reg), int) for reg in args[1:]):
                    value = registers[args[1]] + registers[args[2]]
            except (ValueError, IndexError):
                pass
            if value is None: registers.pop(dest, None)
            else: registers[dest] = value & 0xffffffff
    return hits, calls


def main(args):
    image, base, sections = loaded_image(args.image)
    if args.sdk and not (args.sdk / 'src').is_dir():
        raise ValueError('SDK source directory does not contain src/.')
    generated = sorted(args.generated.glob('*recomp*.cpp'))
    if not generated:
        raise ValueError('No generated recompilation units found.')
    hits, calls, manifest, names, count = [], set(), [], set(), 0
    for path in generated:
        manifest.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for name, code in functions(path):
            count += 1
            names.add(name)
            found, edges = scan_function(name, code, path, image, base, sections)
            hits.extend(found)
            calls.update(edges)
    source_hits, source_files = [], 0
    for root in [ROOT / 'src', ROOT / 'experiments'] + ([args.sdk / 'src', args.sdk / 'include'] if args.sdk else []):
        for path in sorted(root.rglob('*')):
            if path.suffix not in ('.cpp', '.h', '.cc', '.hpp'):
                continue
            source_files += 1
            for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if SOURCE_PATTERN.search(line):
                    source_hits.append(dict(file=str(path), line=number, text=line.strip()))
    labels = Counter(hit['label'] for hit in hits if hit['kind'] == 'float_constant')
    report = dict(schema=1, image_sha256=hashlib.sha256(image).hexdigest(), generated=manifest,
                  functions=count, source_files=source_files,
                  limits='Local PPC address construction (single-definition nonvolatile '
                  'register heuristic at joins; control-flow dominance not proven), direct scalar loads, '
                  'selected immediates and adjacent counter patterns. Indirect/vector loads, '
                  'computed constants and cross-function dataflow can be missed. Numeric '
                  'matches, mutable-data initial values and per-call counters need semantic review.',
                  counts=dict(Counter(hit['kind'] for hit in hits)), float_labels=dict(labels),
                  hits=hits, direct_calls=[list(edge) for edge in sorted(calls) if edge[1] in names],
                  source_hits=source_hits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('functions', 'source_files', 'counts', 'float_labels')}, indent=2))
    print(f'Audit saved to {args.output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Loaded TU0 PE image in RVA layout')
    parser.add_argument('--generated', type=Path, default=ROOT / 'generated')
    parser.add_argument('--sdk', type=Path, help='Optional matching SDK source tree')
    parser.add_argument('--output', type=Path, default=ROOT / 'out/performance/fixed-timing-audit.json')
    main(parser.parse_args())
