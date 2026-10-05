"""Read-only TU0 distant crowd pool samples; asynchronous, not per-update traces.

Pool signature, capacity, stride and fields are audited from 823315E0,
82338220 and 82341740. FrameProbe additionally verifies the loaded DLL hashes.
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
CROWD = 0x82E61D30
POOL = CROWD + 9056
CAPACITY, STRIDE = 1500, 672
FIELDS = ['seconds', 'read_ms', 'step_ms', 'total_ms', 'slot', 'object', 'mode',
          'position_x', 'position_y', 'position_z', 'step_vector_x', 'step_vector_y',
          'step_vector_z', 'movement_step', 'movement_multiplier', 'walk_speed',
          'run_speed', 'segment_steps', 'special', 'model', 'slot_id', 'animation_phase',
          'path_a', 'path_b', 'flags', 'clock_stable']


def capture(args):
    output = args.output.resolve()
    probe = FrameProbe(args.pid, args.metadata)
    samples = rows_written = 0
    created = False
    reason = None
    try:
        header = probe.read_guest(CROWD, 9056)
        if struct.unpack_from('>I', header)[0] != 0xACED0FF0:
            raise ValueError('Unsupported crowd pool signature; load into gameplay first.')
        if struct.unpack_from('>I', header, 9024)[0] > CAPACITY:
            raise ValueError('Unsupported crowd pool capacity.')
        output.mkdir(parents=True, exist_ok=False)
        created = True
        session = dict(pid=args.pid, start_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                       identity=probe.identity, duration_s=args.duration,
                       interval_ms=args.interval_ms,
                       measurement='Read-only asynchronous TU0 crowd samples. Pool mode and fields '
                       'are guest observations, not recovered source names. Bulk reads may '
                       'overlap updates; position differences are not authoritative velocity.')
        (output / 'session.json').write_text(json.dumps(session, indent=2))
        print(f'Observing distant crowd pool for PID {args.pid}: {output}', flush=True)
        origin = deadline = flushed = time.perf_counter()
        with TimerPrecision(), (output / 'crowd.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            while time.perf_counter() - origin < args.duration:
                before = time.perf_counter()
                try:
                    stamp = probe.read_guest(0x82D99128, 8)
                    raw = probe.read_guest(POOL, CAPACITY * STRIDE)
                    stable = stamp == probe.read_guest(0x82D99128, 8)
                except (OSError, RuntimeError) as exc:
                    reason = str(exc)
                    break
                read_ms = (time.perf_counter() - before) * 1000
                step, total = struct.unpack('>2I', stamp)
                for slot in range(CAPACITY):
                    offset = slot * STRIDE
                    mode = struct.unpack_from('>I', raw, offset + 220)[0]
                    if not mode:
                        continue
                    row = dict(seconds=before - origin, read_ms=read_ms, step_ms=step,
                               total_ms=total, slot=slot, object=hex(POOL + offset),
                               mode=mode, clock_stable=int(stable),
                               segment_steps=struct.unpack_from('>I', raw, offset + 176)[0],
                               special=struct.unpack_from('>I', raw, offset + 648)[0],
                               model=struct.unpack_from('>H', raw, offset + 652)[0],
                               slot_id=struct.unpack_from('>H', raw, offset + 658)[0],
                               animation_phase=struct.unpack_from('>f', raw, offset + 228)[0],
                               path_a=hex(struct.unpack_from('>I', raw, offset + 160)[0]),
                               path_b=hex(struct.unpack_from('>I', raw, offset + 164)[0]),
                               flags=raw[offset + 660:offset + 664].hex())
                    row.update(zip(['position_x', 'position_y', 'position_z'],
                                   struct.unpack_from('>3f', raw, offset + 128)))
                    # sub_82331198 adds this vector to position once per call.
                    row.update(zip(['step_vector_x', 'step_vector_y', 'step_vector_z'],
                                   struct.unpack_from('>3f', raw, offset + 112)))
                    row['movement_step'], row['movement_multiplier'] = struct.unpack_from('>2f', raw, offset + 180)
                    row['walk_speed'], row['run_speed'] = struct.unpack_from('>2f', raw, offset + 192)
                    writer.writerow(row)
                    rows_written += 1
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
    completion = dict(samples=samples, rows=rows_written, reason=reason,
                      end_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    if created:
        (output / 'completion.json').write_text(json.dumps(completion, indent=2))
    print(json.dumps(completion), flush=True)
    return 0 if samples else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, default=ROOT / 'out/build/win-amd64-release/offsets.json')
    parser.add_argument('--duration', type=float, default=120)
    parser.add_argument('--interval-ms', type=float, default=16)
    args = parser.parse_args()
    if not 0 < args.duration <= 3600 or not 8 <= args.interval_ms <= 1000:
        parser.error('Use duration in (0, 3600] seconds and interval in [8, 1000] ms.')
    raise SystemExit(capture(args))
