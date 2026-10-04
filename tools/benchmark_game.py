"""Record/replay a controller route, or benchmark a stationary camera sweep.

Uses the physical XInput controller for recording and the game's opt-in local
automation controller for playback. Only standard-library Python is required.
"""
import argparse
import bisect
import csv
import hashlib
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from benchmark_windows import PAD_FIELDS, FrameProbe, TimerPrecision, XInput
from control_game import Controller

ROOT = Path(__file__).resolve().parents[1]
NEUTRAL = dict.fromkeys(PAD_FIELDS, 0)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_route(route):
    if not isinstance(route, dict):
        raise ValueError('Route must be a JSON object')
    if route.get('schema') != 1 or not number(route.get('duration_s')) or not 0 < route['duration_s'] <= 600:
        raise ValueError('Route requires schema 1 and a finite duration in (0, 600] seconds')
    warmup = route.get('warmup_s', 0)
    if not number(warmup) or not 0 <= warmup < route['duration_s']:
        raise ValueError('Route warmup must be within its duration')
    previous = -1
    if not isinstance(route.get('samples'), list) or not route['samples']:
        raise ValueError('Route needs controller samples')
    for sample in route['samples']:
        if not isinstance(sample, dict) or not isinstance(sample.get('pad'), dict):
            raise ValueError('Samples must contain a controller object')
        t = sample.get('t')
        if not number(t) or not previous < t < route['duration_s']:
            raise ValueError('Sample times must strictly increase within the route')
        if previous == -1 and t != 0:
            raise ValueError('First sample must start at zero')
        previous = t
        if set(sample.get('pad', {})) != set(PAD_FIELDS):
            raise ValueError('Every sample requires all controller fields')
        for field, value in sample['pad'].items():
            lower, upper = ((0, 65535) if field == 'buttons' else
                            (0, 255) if field in ('lt', 'rt') else (-32768, 32767))
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f'Invalid controller value: {field}')
    return route


def route_state(route, timestamps, elapsed):
    if elapsed >= route['duration_s']:
        return NEUTRAL
    return route['samples'][max(0, bisect.bisect_right(timestamps, elapsed) - 1)]['pad']


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def summarize(rows):
    if len(rows) < 2:
        return {'duration_s': 0, 'frames': 0, 'guest_submission_fps': None}
    duration = rows[-1]['t'] - rows[0]['t']
    frames = rows[-1]['frame'] - rows[0]['frame']
    changes = [b for a, b in zip(rows, rows[1:]) if b['frame'] != a['frame']]
    intervals = [(b['t'] - a['t']) * 1000 for a, b in zip(changes, changes[1:])
                 if b['frame'] - a['frame'] == 1]
    gaps = [b['t'] - a['t'] for a, b in zip([rows[0]] + changes, changes + [rows[-1]])]
    return {
        'duration_s': duration, 'frames': frames,
        'guest_submission_fps': frames / duration if duration > 0 else None,
        'frame_ms_p50': percentile(intervals, .50),
        'frame_ms_p95': percentile(intervals, .95),
        'frame_ms_p99': percentile(intervals, .99),
        'frame_ms_max': max(intervals, default=None),
        'intervals_over_50ms': sum(value > 50 for value in intervals),
        'observed_intervals': len(intervals),
        'frames_not_individually_observed': sum(max(0, b['frame'] - a['frame'] - 1)
                                               for a, b in zip(rows, rows[1:])),
        'longest_observed_submission_gap_ms': max(gaps, default=0) * 1000,
        'trailing_no_submission_ms': (rows[-1]['t'] - (changes[-1]['t'] if changes else rows[0]['t'])) * 1000,
    }


def execute(args, route=None):
    controller = None
    probe = None
    rows, samples = [], []
    output = None
    completed = False
    cpu_start = None
    elapsed = 0
    late_max = 0
    warmup_arg = getattr(args, 'warmup', None)
    warmup = warmup_arg if warmup_arg is not None else (route.get('warmup_s', 0) if route else 0)
    duration = route['duration_s'] if route else getattr(args, 'seconds', math.inf)
    if args.mode != 'live' and not 0 <= warmup < duration:
        raise ValueError('Warmup must be shorter than the run')
    if args.mode == 'record' and args.route.exists():
        raise FileExistsError(f'Recording exists: {args.route}; choose a new filename')
    try:
        physical = XInput(args.controller) if args.mode in ('live', 'record') else None
        if args.mode in ('live', 'replay', 'sweep') or (args.mode == 'record' and args.bridge):
            if not args.pid:
                raise ValueError('This mode requires --pid of a game launched with --automation=true')
            controller = Controller(args.pid)
        if args.mode != 'live' and args.pid:
            probe = FrameProbe(args.pid, args.offsets)
        if args.mode != 'live':
            output = args.output or ROOT / 'out' / 'benchmark' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            output.mkdir(parents=True, exist_ok=False)
        print(f'{args.mode}: starts in {args.countdown:g}s; Ctrl+C releases controls.', flush=True)
        time.sleep(args.countdown)
        print('GO', flush=True)
        started = time.perf_counter()
        input_next = started
        last_ack = controller.read_int(40) if controller else 0
        ack_time = started
        timestamps = [sample['t'] for sample in route['samples']] if route else []
        while True:
            now = time.perf_counter()
            elapsed = now - started
            if args.mode != 'live' and elapsed >= duration:
                completed = True
                break
            if now >= input_next:
                late_max = max(late_max, (now - input_next) * 1000)
                if physical:
                    pad = physical.read()
                elif route:
                    pad = route_state(route, timestamps, elapsed)
                else:
                    # In-game look camera, not a detached/free camera. The player
                    # remains in the populated area so AI/streaming stay active.
                    pad = {**NEUTRAL, 'rx': args.speed if int(elapsed / args.segment) % 2 == 0 else -args.speed}
                if args.mode == 'record' and (not samples or pad != samples[-1]['pad']):
                    samples.append({'t': elapsed if samples else 0, 'pad': pad})
                if controller:
                    ack = controller.read_int(40)
                    if ack != last_ack:
                        last_ack, ack_time = ack, now
                    if args.mode in ('replay', 'sweep') and now - ack_time > 3:
                        raise RuntimeError('No game input poll for three seconds; benchmark aborted')
                    controller.state(**pad, lease_ms=250)
                # Skip missed ticks rather than burst multiple outdated inputs.
                input_next += (math.floor((now - input_next) / .01) + 1) * .01
            if probe and elapsed >= warmup:
                frame, done = probe.read()
                sampled_at = time.perf_counter() - started
                if not rows:
                    cpu_start = probe.cpu_seconds()
                if rows and frame < rows[-1]['frame']:
                    raise RuntimeError('Frame counter moved backwards; incompatible backend or runtime restarted')
                if not rows or frame != rows[-1]['frame']:
                    rows.append({'t': sampled_at, 'frame': frame, 'completed': done})
            time.sleep(.002)
    finally:
        failure = sys.exc_info()[1]
        if 'started' in locals():
            elapsed = time.perf_counter() - started
        # Release before file I/O, and close all handles even on interruption.
        try:
            if controller:
                controller.state(lease_ms=0)
        finally:
            if controller:
                controller.close()
        cpu_end = None
        if probe:
            try:
                if rows:
                    frame, done = probe.read()
                    rows.append({'t': time.perf_counter() - started, 'frame': frame, 'completed': done})
                    cpu_end = probe.cpu_seconds()
            except OSError:
                pass  # Preserve the observations if the target exited.
            finally:
                probe.close()
        if args.mode == 'record' and samples:
            recording = {'schema': 1, 'duration_s': min(elapsed, duration),
                         'warmup_s': min(warmup, elapsed / 2) if elapsed <= warmup else warmup,
                         'samples': samples, 'complete': completed,
                         'note': args.note, 'input': 'raw XInput, sampled at 100 Hz'}
            validate_route(recording)
            args.route.parent.mkdir(parents=True, exist_ok=True)
            with args.route.open('x', encoding='utf-8') as file:
                json.dump(recording, file, indent=2)
            print(f'Route: {args.route.resolve()}', flush=True)
        if output:
            report = {**summarize(rows), 'mode': args.mode, 'complete': completed,
                      'error': f'{type(failure).__name__}: {failure}' if failure else None,
                      'pid': args.pid, 'warmup_s': warmup, 'note': args.note,
                      'route': str(args.route.resolve()) if args.mode in ('record', 'replay') else None,
                      'input_schedule_max_lateness_ms': late_max,
                      'measurement': 'Guest D3D12 frame submissions, polled every ~2 ms; not display latency',
                      'probe': probe.identity if probe else None}
            if args.mode in ('record', 'replay') and args.route.is_file():
                report['route_sha256'] = hashlib.sha256(args.route.read_bytes()).hexdigest()
                report['route_recording_complete'] = recording['complete'] if args.mode == 'record' else route.get('complete')
            if cpu_start is not None and cpu_end is not None and report['duration_s'] > 0:
                report['cpu_core_equivalents'] = (cpu_end - cpu_start) / report['duration_s']
            with (output / 'frames.csv').open('w', newline='', encoding='utf-8') as file:
                writer = csv.DictWriter(file, fieldnames=['t', 'frame', 'completed'])
                writer.writeheader()
                writer.writerows(rows)
            (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2), flush=True)
            print(f'Results: {output.resolve()}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    for mode in ('live', 'record', 'replay', 'sweep'):
        sub = commands.add_parser(mode)
        sub.add_argument('--pid', type=int, required=mode in ('live', 'replay', 'sweep'))
        sub.add_argument('--countdown', type=float, default=3)
        sub.add_argument('--controller', type=int, choices=range(4), default=0)
        if mode != 'live':
            sub.add_argument('--output', type=Path)
            sub.add_argument('--offsets', type=Path, default=ROOT / 'out' / 'benchmark' / 'offsets.json')
            sub.add_argument('--warmup', type=float)
            sub.add_argument('--note', default='')
        if mode in ('record', 'replay'):
            sub.add_argument('--route', type=Path, required=True)
        if mode == 'record':
            sub.add_argument('--bridge', action='store_true', help='Forward physical controls to an automation instance')
        if mode in ('record', 'sweep'):
            sub.add_argument('--seconds', type=float, default=60)
        if mode == 'sweep':
            sub.add_argument('--speed', type=int, default=12000)
            sub.add_argument('--segment', type=float, default=10)
    args = parser.parse_args()
    if not number(args.countdown) or not 0 <= args.countdown <= 60:
        parser.error('Countdown must be within 0..60 seconds')
    if hasattr(args, 'seconds') and (not number(args.seconds) or not 0 < args.seconds <= 600):
        parser.error('Duration must be within (0, 600] seconds')
    if args.mode == 'sweep' and (not 1 <= args.speed <= 32767 or not number(args.segment) or args.segment <= 0):
        parser.error('Sweep needs speed 1..32767 and a positive finite segment duration')
    try:
        route = validate_route(json.loads(args.route.read_text(encoding='utf-8'))) if args.mode == 'replay' else None
        with TimerPrecision():
            execute(args, route)
    except KeyboardInterrupt:
        print('Stopped; controller released.')
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
