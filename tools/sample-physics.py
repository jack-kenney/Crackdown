"""Read-only TU0 movement, Havok clock, CPU and SDK frame observations.

These are asynchronous memory samples, not an authoritative per-update trace.
FrameProbe checks the loaded renderer/runtime against the supplied DLL hashes.
Known guest virtual functions additionally guard the interpreted TU0 layouts.
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
VECTOR_OFFSETS = {
    'agent_position': 320,
    'agent_velocity': 1296,  # Virtual getter 188: sub_822BB570.
    'controller_position': 1344 + 336,
    'controller_derived_velocity': 1344 + 352,
    'controller_velocity': 1344 + 560,
    'controller_recent_velocity': 1344 + 576,
    'controller_average_velocity': 1344 + 624,
}
FIELDS = [
    'seconds', 'read_ms', 'frame', 'completed', 'cpu_seconds',
    'engine', 'fixed', 'paused', 'logical_frame', 'sampled_ms',
    'wall_cursor_ms', 'committed_ms', 'discarded_ms', 'step_ms',
    'simulation_total_ms', 'main_scale', 'fixed_scale', 'player', 'agent',
    'camera', 'physics_world', 'physics_simulation', 'physics_step_s',
    'physics_time_s', 'physics_step_start_s', 'physics_step_end_s',
    'support_entity', 'controller_flags',
] + [f'{name}_{axis}' for name in [*VECTOR_OFFSETS, 'proxy_position',
                                  'camera_position', 'phantom_position']
     for axis in 'xyz'] + [
    'clock_snapshot_stable', 'pointers_stable', 'agent_position_stable',
    'physics_snapshot_stable', 'error',
]


class PhysicsProbe:
    def __init__(self, probe):
        self.probe = probe

    def u32(self, address):
        return struct.unpack('>I', self.probe.read_guest(address, 4))[0]

    def vector(self, row, name, address):
        raw = self.probe.read_guest(address, 12)
        row.update(zip((f'{name}_{axis}' for axis in 'xyz'),
                       struct.unpack('>3f', raw)))
        return raw

    def sample(self):
        p = self.probe
        stamp = p.read_guest(0x82D99128, 8)
        step, total = struct.unpack('>2I', stamp)
        engine = self.u32(0x82DE25C0)
        player = self.u32(0x82DE3F3C)
        world = self.u32(0x82DE3EEC)
        if not engine or not player or not world:
            raise ValueError('Gameplay pointers unavailable; load into gameplay.')
        body = self.u32(player + 292)
        if not body:
            raise ValueError('Player agent unavailable.')
        agent = body - 32
        agent_vtable = self.u32(agent)
        if (self.u32(agent_vtable + 180) != 0x821E0558 or
                self.u32(agent_vtable + 188) != 0x822BB570):
            raise ValueError('Unsupported agent position/velocity layout.')
        simulation = self.u32(world + 8)
        if not simulation or self.u32(self.u32(simulation) + 8) != 0x829BD048:
            raise ValueError('Unsupported Havok simulation layout.')
        data = p.read_guest(engine, 68)
        words = struct.unpack('>17I', data)
        frame, completed = p.read()
        row = dict(frame=frame, completed=completed, cpu_seconds=p.cpu_seconds(),
                   engine=hex(engine), fixed=data[13], paused=data[14],
                   logical_frame=words[4], sampled_ms=words[5],
                   wall_cursor_ms=words[8], committed_ms=words[9],
                   discarded_ms=words[16], step_ms=step,
                   simulation_total_ms=total, player=hex(player), agent=hex(agent),
                   physics_world=hex(world), physics_simulation=hex(simulation))
        row['main_scale'] = struct.unpack('>f', p.read_guest(0x82CFE4B4, 4))[0]
        row['fixed_scale'] = struct.unpack('>f', p.read_guest(0x82C63578, 4))[0]
        position = None
        for name, offset in VECTOR_OFFSETS.items():
            raw = self.vector(row, name, agent + offset)
            if name == 'agent_position':
                position = raw
        self.vector(row, 'proxy_position', player + 304)
        manager = self.u32(0x82DE2574)
        camera = self.u32(manager + 272) if manager else 0
        row['camera'] = hex(camera)
        if camera:
            self.vector(row, 'camera_position', camera + 320)
        component = agent + 1344
        row['support_entity'] = hex(self.u32(component + 460))
        row['controller_flags'] = p.read_guest(component + 1001, 3).hex()
        phantom = self.u32(component + 660)
        if phantom:
            # sub_829D3550 returns this position; no guest functions are called.
            shape = self.u32(phantom + 48)
            motion = self.u32(shape + 28) if shape else 0
            if motion:
                self.vector(row, 'phantom_position', motion + 48)
        physics_times = p.read_guest(world + 12, 16)
        physics_step = p.read_guest(simulation + 8, 4)
        row.update(zip(('physics_time_s', 'physics_step_start_s',
                        'physics_step_end_s'), struct.unpack('>4f', physics_times)[1:]))
        row['physics_step_s'] = struct.unpack('>f', physics_step)[0]
        row['physics_snapshot_stable'] = int(
            physics_times == p.read_guest(world + 12, 16) and
            physics_step == p.read_guest(simulation + 8, 4))
        row['agent_position_stable'] = int(position == p.read_guest(agent + 320, 12))
        row['clock_snapshot_stable'] = int(stamp == p.read_guest(0x82D99128, 8))
        row['pointers_stable'] = int(
            engine == self.u32(0x82DE25C0) and player == self.u32(0x82DE3F3C) and
            body == self.u32(player + 292) and world == self.u32(0x82DE3EEC) and
            simulation == self.u32(world + 8))
        return row


def capture(args):
    output = args.output.resolve()
    probe = FrameProbe(args.pid, args.metadata)
    count = errors = 0
    reason = None
    try:
        output.mkdir(parents=True, exist_ok=False)
        origin = time.perf_counter()
        started = dt.datetime.now(dt.timezone.utc)
        session = dict(pid=args.pid, start_utc=started.isoformat(),
                       duration_s=args.duration, interval_ms=args.interval_ms,
                       identity=probe.identity,
                       measurement='Read-only asynchronous TU0 samples. CPU is cumulative process CPU time. '
                       'Frame counters measure guest submissions. Position differences are not '
                       'authoritative velocity. Stability checks do not make snapshots atomic.')
        (output / 'session.json').write_text(json.dumps(session, indent=2))
        observer = PhysicsProbe(probe)
        print(f'Capturing PID {args.pid} to {output}', flush=True)
        with TimerPrecision(), (output / 'observations.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            deadline = flushed = origin
            while time.perf_counter() - origin < args.duration:
                before = time.perf_counter()
                row = {'seconds': before - origin}
                try:
                    row.update(observer.sample())
                except (OSError, RuntimeError, ValueError, struct.error) as exc:
                    row['error'] = str(exc)
                    errors += 1
                    try:
                        probe.read()
                    except (OSError, RuntimeError) as ended:
                        reason = str(ended)
                        break
                after = time.perf_counter()
                row['read_ms'] = (after - before) * 1000
                writer.writerow(row)
                count += 1
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
    completion = dict(samples=count, sample_errors=errors, end_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                      reason=reason)
    (output / 'completion.json').write_text(json.dumps(completion, indent=2))
    print(json.dumps(completion), flush=True)
    return 1 if not count or errors == count else 0


def mark(args):
    output = args.output.resolve()
    session = json.loads((output / 'session.json').read_text())
    now = dt.datetime.now(dt.timezone.utc)
    estimated = now - dt.timedelta(seconds=args.ago)
    event = dict(received_utc=now.isoformat(), estimated_event_utc=estimated.isoformat(),
                 estimated_seconds=(estimated - dt.datetime.fromisoformat(session['start_utc'])).total_seconds(),
                 uncertainty_s=args.uncertainty, note=args.note)
    with (output / 'event-markers.jsonl').open('a') as stream:
        stream.write(json.dumps(event) + '\n')
    print(json.dumps(event), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sample = sub.add_parser('capture')
    sample.add_argument('--pid', type=int, required=True)
    sample.add_argument('--output', type=Path, required=True)
    sample.add_argument('--metadata', type=Path, default=ROOT / 'out/build/win-amd64-release/offsets.json')
    sample.add_argument('--duration', type=float, default=900)
    sample.add_argument('--interval-ms', type=float, default=8)
    sample.set_defaults(run=capture)
    marker = sub.add_parser('mark')
    marker.add_argument('--output', type=Path, required=True)
    marker.add_argument('--ago', type=float, default=0)
    marker.add_argument('--uncertainty', type=float, default=10)
    marker.add_argument('--note', default='User observed a physics anomaly.')
    marker.set_defaults(run=mark)
    args = parser.parse_args()
    if args.command == 'capture' and (not 0 < args.duration <= 3600 or not 4 <= args.interval_ms <= 1000):
        parser.error('Use a duration in (0, 3600] seconds and an interval in [4, 1000] ms.')
    if args.command == 'mark' and (args.ago < 0 or args.uncertainty < 0):
        parser.error('Lookback and uncertainty must be nonnegative.')
    return args.run(args)


if __name__ == '__main__':
    raise SystemExit(main())
