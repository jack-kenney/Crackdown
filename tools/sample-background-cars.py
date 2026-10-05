"""Read-only TU0 low-detail car samples, including cars outside the draw set.

Fields are audited from 82333310, 82333538, 82332EC8 and 82343ED8.
Asynchronous reads are observations, not exact per-update velocity traces.
"""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path
import struct
import time
from benchmark_windows import FrameProbe, TimerPrecision

ROOT = Path(__file__).resolve().parents[1]
MANAGER, POOL, STRIDE, CAPACITY = 0x82FDE4B0, 0x82FDF470, 480, 1000
FIELDS = ['seconds', 'read_ms', 'step_ms', 'total_ms', 'clock_stable', 'slot',
          'object', 'allocated', 'path_a', 'path_b', 'path_ref', 'segment_steps',
          'movement_step', 'movement_multiplier', 'draw_alpha', 'visibility_bits']
FIELDS += [f'{name}_{axis}' for name in ('position', 'target_direction',
                                      'direction', 'nominal_displacement') for axis in 'xyz']


def capture(args):
    probe = FrameProbe(args.pid, args.metadata)
    output = args.output.resolve()
    samples = rows = 0
    reason = None
    try:
        header = probe.read_guest(MANAGER, 4032)
        if struct.unpack_from('>I', header, 4)[0] != 0xACED0FF0:
            raise ValueError('Unsupported low-detail car pool; load gameplay first.')
        output.mkdir(parents=True, exist_ok=False)
        session = dict(pid=args.pid, start_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                       identity=probe.identity, duration_s=args.duration,
                       interval_ms=args.interval_ms, slot_stride=args.slot_stride,
                       measurement=__doc__)
        (output / 'session.json').write_text(json.dumps(session, indent=2))
        print(f'Observing background cars PID {args.pid}: {output}', flush=True)
        origin = deadline = flushed = time.perf_counter()
        with TimerPrecision(), (output / 'cars.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            while time.perf_counter() - origin < args.duration:
                before = time.perf_counter()
                try:
                    stamp = probe.read_guest(0x82D99128, 8)
                    header = probe.read_guest(MANAGER, 4032)
                    count = struct.unpack_from('>I', header, 20)[0]
                    if count > CAPACITY:
                        raise RuntimeError('Car allocation count exceeds capacity.')
                    allocated = set(struct.unpack_from(f'>{count}I', header, 32))
                    raw = probe.read_guest(POOL, STRIDE * CAPACITY)
                    stable = stamp == probe.read_guest(0x82D99128, 8)
                except (OSError, RuntimeError) as exc:
                    reason = str(exc)
                    break
                step, total = struct.unpack('>2I', stamp)
                read_ms = (time.perf_counter() - before) * 1000
                for slot in range(0, CAPACITY, args.slot_stride):
                    offset = slot * STRIDE
                    address = POOL + offset
                    if address not in allocated:
                        continue
                    row = dict(seconds=before-origin, read_ms=read_ms, step_ms=step,
                               total_ms=total, clock_stable=int(stable), slot=slot,
                               object=hex(address), allocated=raw[offset+112],
                               segment_steps=struct.unpack_from('>I', raw, offset+124)[0],
                               draw_alpha=struct.unpack_from('>f', raw, offset+256)[0],
                               visibility_bits=hex(struct.unpack_from('>H', raw, offset+272)[0]))
                    for key, field in [('path_a', 136), ('path_b', 140), ('path_ref', 144)]:
                        row[key] = hex(struct.unpack_from('>I', raw, offset+field)[0])
                    row['movement_step'], row['movement_multiplier'] = struct.unpack_from('>2f', raw, offset+116)
                    for name, field in [('position', 64), ('target_direction', 16),
                                        ('direction', 32), ('nominal_displacement', 48)]:
                        row.update(zip((f'{name}_{axis}' for axis in 'xyz'),
                                       struct.unpack_from('>3f', raw, offset+field)))
                    writer.writerow(row)
                    rows += 1
                samples += 1
                after = time.perf_counter()
                if after - flushed >= 1:
                    stream.flush()
                    flushed = after
                deadline += args.interval_ms / 1000
                if deadline <= after:
                    deadline = after + args.interval_ms / 1000
                time.sleep(max(0, deadline - time.perf_counter()))
    except KeyboardInterrupt:
        reason = 'Observer interrupted; game unaffected.'
    finally:
        probe.close()
    completion = dict(samples=samples, rows=rows, reason=reason,
                      end_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    if output.exists():
        (output / 'completion.json').write_text(json.dumps(completion, indent=2))
    print(json.dumps(completion), flush=True)
    return 0 if samples else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, default=ROOT / 'out/build/win-amd64-release/offsets.json')
    parser.add_argument('--duration', type=float, default=20)
    parser.add_argument('--interval-ms', type=float, default=16)
    parser.add_argument('--slot-stride', type=int, default=8)
    args = parser.parse_args()
    if not 0 < args.duration <= 3600 or not 8 <= args.interval_ms <= 1000 or not 1 <= args.slot_stride <= CAPACITY:
        parser.error('Use duration (0,3600], interval [8,1000] ms and slot stride [1,1000].')
    raise SystemExit(capture(args))
