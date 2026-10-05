"""Read-only SDK frame-counter, CPU and optional device GPU observations.

Frame polling runs independently of thread enumeration, guest-state reads and
nvidia-smi. Counter transitions are sampled observations, not presentation
timestamps. DLL hashes and process creation time must match in every worker.
"""
import argparse
import csv
import ctypes as C
import json
import math
import multiprocessing as MP
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

from benchmark_windows import FrameProbe, TimerPrecision


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def summarize_frames(rows):
    """Bound transition intervals by the adjacent read windows, not invented events."""
    if len(rows) < 2:
        return {'samples': len(rows), 'duration_s': 0, 'frames': 0, 'guest_submission_fps': None}
    midpoint = lambda row: (row['read_start_s'] + row['read_end_s']) * .5
    duration = midpoint(rows[-1]) - midpoint(rows[0])
    frames = rows[-1]['frame'] - rows[0]['frame']
    regressions = 0
    missed = 0
    transitions = []
    poll_gaps = []
    for before, after in zip(rows, rows[1:]):
        poll_gaps.append((after['read_start_s'] - before['read_start_s']) * 1000)
        advance = after['frame'] - before['frame']
        if advance < 0:
            regressions += 1
        elif advance:
            missed += max(0, advance - 1)
            transitions.append({'frame': after['frame'], 'advance': advance,
                                'lo': before['read_start_s'], 'hi': after['read_end_s']})
    estimated = []
    lower = []
    upper = []
    for before, after in zip(transitions, transitions[1:]):
        # Multiframe jumps provide no individually observed event timing.
        if before['advance'] != 1 or after['advance'] != 1 or after['frame'] - before['frame'] != 1:
            continue
        estimated.append((after['lo'] + after['hi'] - before['lo'] - before['hi']) * 500)
        lower.append(max(0, after['lo'] - before['hi']) * 1000)
        upper.append((after['hi'] - before['lo']) * 1000)
    result = {
        'samples': len(rows), 'duration_s': duration, 'frames': frames,
        'guest_submission_fps': frames / duration if duration > 0 and not regressions else None,
        'counter_regressions': regressions,
        'frames_not_individually_observed': missed,
        'individually_observed_intervals': len(estimated),
        'poll_gap_ms_p50': percentile(poll_gaps, .5),
        'poll_gap_ms_p95': percentile(poll_gaps, .95),
        'poll_gap_ms_max': max(poll_gaps, default=None),
        'read_latency_ms_max': max((r['read_end_s'] - r['read_start_s']) * 1000 for r in rows),
        'measurement': 'SDK guest frame-counter increments; physical presentations are not measured',
        'interval_method': 'Midpoints of bounded transition windows; missed transitions excluded from interval percentiles',
    }
    for label, fraction in [('p50', .5), ('p95', .95), ('p99', .99)]:
        result[f'observed_interval_ms_{label}'] = percentile(estimated, fraction)
        result[f'observed_interval_ms_{label}_lower_bound'] = percentile(lower, fraction)
        result[f'observed_interval_ms_{label}_upper_bound'] = percentile(upper, fraction)
    return result


class ThreadEntry(C.Structure):
    _fields_ = [('size', C.c_uint32), ('usage', C.c_uint32), ('tid', C.c_uint32),
                ('owner', C.c_uint32), ('base_priority', C.c_int32),
                ('delta_priority', C.c_int32), ('flags', C.c_uint32)]


class WindowsCpu:
    def __init__(self, probe, pid):
        self.probe, self.pid = probe, pid
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateToolhelp32Snapshot.argtypes = [C.c_uint32, C.c_uint32]
        self.kernel.CreateToolhelp32Snapshot.restype = C.c_void_p
        self.kernel.Thread32First.argtypes = [C.c_void_p, C.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.argtypes = [C.c_void_p, C.POINTER(ThreadEntry)]
        self.kernel.OpenThread.argtypes = [C.c_uint32, C.c_int, C.c_uint32]
        self.kernel.OpenThread.restype = C.c_void_p
        self.kernel.GetThreadTimes.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4
        self.kernel.CloseHandle.argtypes = [C.c_void_p]
        self.kernel.QueryFullProcessImageNameW.argtypes = [C.c_void_p, C.c_uint32, C.c_wchar_p,
                                                         C.POINTER(C.c_uint32)]

    def identity(self):
        times = [C.c_uint64() for _ in range(4)]
        if not self.probe.kernel.GetProcessTimes(self.probe.handle, *(C.byref(v) for v in times)):
            raise C.WinError(C.get_last_error())
        size = C.c_uint32(32768)
        path = C.create_unicode_buffer(size.value)
        if not self.kernel.QueryFullProcessImageNameW(self.probe.handle, 0, path, C.byref(size)):
            raise C.WinError(C.get_last_error())
        return {'pid': self.pid, 'created_filetime': times[0].value, 'executable': path.value,
                'probe': self.probe.identity}

    def threads(self, origin):
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        result = {}
        try:
            entry = ThreadEntry()
            entry.size = C.sizeof(entry)
            valid = self.kernel.Thread32First(snapshot, C.byref(entry))
            while valid:
                if entry.owner == self.pid:
                    handle = self.kernel.OpenThread(0x0800, False, entry.tid)  # QUERY_LIMITED_INFORMATION
                    if handle:
                        try:
                            values = [C.c_uint64() for _ in range(4)]
                            before = time.perf_counter() - origin
                            ok = self.kernel.GetThreadTimes(handle, *(C.byref(v) for v in values))
                            after = time.perf_counter() - origin
                            if ok:
                                result[(entry.tid, values[0].value)] = {
                                    'seconds': (values[2].value + values[3].value) / 10_000_000,
                                    'kernel_seconds': values[2].value / 10_000_000,
                                    'user_seconds': values[3].value / 10_000_000,
                                    'read_start_s': before, 'read_end_s': after}
                        finally:
                            self.kernel.CloseHandle(handle)
                entry.size = C.sizeof(entry)
                valid = self.kernel.Thread32Next(snapshot, C.byref(entry))
            error = C.get_last_error()
            if error != 18:  # ERROR_NO_MORE_FILES
                raise C.WinError(error)
        finally:
            self.kernel.CloseHandle(snapshot)
        return result


def guest_state(probe, origin):
    before = time.perf_counter() - origin
    try:
        u32 = lambda address: struct.unpack('>I', probe.read_guest(address, 4))[0]
        engine = u32(0x82DE25C0)
        player = u32(0x82DE3F3C)
        body = u32(player + 292) if player else 0
        holder = u32(0x82DE2574)
        camera = u32(holder + 272) if holder else 0
        result = {'engine': engine, 'player': player, 'body': body,
                  'position': struct.unpack('>3f', probe.read_guest(body + 288, 12)) if body else None,
                  'camera': struct.unpack('>3f', probe.read_guest(camera + 320, 12)) if camera else None,
                  'delta_ms': u32(0x82D99128), 'discarded_ms': u32(engine + 64) if engine else None,
                  'logical_frame': u32(engine + 16) if engine else None}
        # A scene transition can change pointers between these independent reads.
        result['pointer_snapshot_stable'] = (u32(0x82DE25C0) == engine and
                                            u32(0x82DE3F3C) == player and
                                            u32(0x82DE2574) == holder and
                                            (not player or u32(player + 292) == body) and
                                            (not holder or u32(holder + 272) == camera))
    except (OSError, RuntimeError, ValueError) as error:
        result = {'error': str(error)}
    result.update(read_start_s=before, read_end_s=time.perf_counter() - origin)
    return result


def cpu_snapshot(probe, cpu, origin):
    before = time.perf_counter() - origin
    seconds = probe.cpu_seconds()
    after = time.perf_counter() - origin
    threads = cpu.threads(origin)
    return {'read_start_s': before, 'read_end_s': after, 'seconds': seconds,
            'threads': threads, 'guest': guest_state(probe, origin)}


def cpu_delta(before, after):
    midpoint = lambda value: (value['read_start_s'] + value['read_end_s']) * .5
    elapsed = midpoint(after) - midpoint(before)
    threads = []
    for (tid, created), value in after['threads'].items():
        old = before['threads'].get((tid, created))
        interval = midpoint(value) - midpoint(old) if old else 0
        threads.append({'tid': tid, 'created_filetime': created,
                        'cpu_cores': (value['seconds'] - old['seconds']) / interval if interval > 0 else None,
                        'kernel_cores': (value['kernel_seconds'] - old['kernel_seconds']) / interval if interval > 0 else None,
                        'user_cores': (value['user_seconds'] - old['user_seconds']) / interval if interval > 0 else None,
                        'first_observation': old is None})
    threads.sort(key=lambda row: row['cpu_cores'] if row['cpu_cores'] is not None else -1, reverse=True)
    return {'read_start_s': after['read_start_s'], 'read_end_s': after['read_end_s'],
            'interval_s': elapsed,
            'cpu_cores': (after['seconds'] - before['seconds']) / elapsed if elapsed > 0 else None,
            'threads': threads, 'guest': after['guest']}


def worker(kind, pid, offsets, output, expected, origin_value, start_event, stop_event, ready):
    probe = None
    data = {}
    started = False
    try:
        probe = FrameProbe(pid, offsets)
        cpu = WindowsCpu(probe, pid)
        identity = cpu.identity()
        if identity != expected:
            raise RuntimeError('Worker PID creation time, executable or DLL hashes differ from the frame sampler')
        gpu_command = shutil.which('nvidia-smi') if kind == 'gpu' else None
        if kind == 'gpu' and gpu_command is None:
            raise RuntimeError('--gpu requires nvidia-smi in PATH')
        ready.put({'worker': kind, 'ready': True})
        started = True
        start_event.wait()
        if stop_event.is_set():
            return
        origin = origin_value.value
        if kind == 'cpu':
            first = cpu_snapshot(probe, cpu, origin)
            before = first
            rows = []
            deadline = origin + 1
            while not stop_event.wait(max(0, deadline - time.perf_counter())):
                after = cpu_snapshot(probe, cpu, origin)
                rows.append(cpu_delta(before, after))
                before = after
                deadline = max(deadline + 1, time.perf_counter())
            last = cpu_snapshot(probe, cpu, origin)
            rows.append(cpu_delta(before, last))
            duration = (last['read_start_s'] + last['read_end_s'] -
                        first['read_start_s'] - first['read_end_s']) * .5
            data = {'identity': identity, 'rows': rows,
                    'cpu_cores': (last['seconds'] - first['seconds']) / duration if duration > 0 else None,
                    'cpu_interval_s': duration, 'guest_before': first['guest'], 'guest_after': last['guest'],
                    'measurement': 'CPU seconds / wall seconds; per-thread keys include creation time; new/exited threads can lack an interval'}
        else:
            rows = []
            while not stop_event.is_set():
                before = time.perf_counter() - origin
                try:
                    result = subprocess.run([gpu_command,
                        '--query-gpu=index,name,timestamp,utilization.gpu,utilization.memory,power.draw,clocks.current.graphics',
                        '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    row = {'returncode': result.returncode, 'csv': result.stdout.strip(),
                           'stderr': result.stderr.strip()}
                except (OSError, subprocess.TimeoutExpired) as error:
                    row = {'error': str(error)}
                row.update(read_start_s=before, read_end_s=time.perf_counter() - origin)
                rows.append(row)
                stop_event.wait(2)
            data = {'identity': identity, 'rows': rows,
                    'measurement': 'Device-wide nvidia-smi samples include every process using each GPU'}
    except BaseException as error:
        data = {'error': str(error), 'worker': kind}
        if not started:
            ready.put(data)
    finally:
        if probe:
            probe.close()
        (Path(output) / f'{kind}.json').write_text(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--seconds', required=True, type=float)
    parser.add_argument('--offsets', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New output directory; existing paths are refused')
    parser.add_argument('--gpu', action='store_true', help='Collect device-wide nvidia-smi samples in a separate worker')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Read-only renderer sampling requires Windows')
    if args.pid <= 0 or not math.isfinite(args.seconds) or not 0 < args.seconds <= 600:
        parser.error('PID must be positive and duration must be finite, within (0, 600] seconds')
    offsets = args.offsets.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    probe = None
    processes = []
    polls = []
    context = MP.get_context('spawn')
    origin_value = context.Value('d', 0)
    start_event, stop_event = context.Event(), context.Event()
    ready = context.Queue()
    failure = None
    identity = {}
    try:
        probe = FrameProbe(args.pid, offsets)
        identity = WindowsCpu(probe, args.pid).identity()
        for kind in (['cpu', 'gpu'] if args.gpu else ['cpu']):
            process = context.Process(target=worker, args=(kind, args.pid, str(offsets), str(output),
                                      identity, origin_value, start_event, stop_event, ready))
            process.start()
            processes.append(process)
        for _ in processes:
            status = ready.get(timeout=15)
            if not status.get('ready'):
                raise RuntimeError(f"Worker initialization failed: {status}")
        with TimerPrecision():
            origin = time.perf_counter()
            origin_value.value = origin
            start_event.set()
            deadline = origin
            while True:
                before = time.perf_counter()
                frame, completed = probe.read()
                after = time.perf_counter()
                polls.append({'read_start_s': before - origin, 'read_end_s': after - origin,
                              'frame': frame, 'completed': completed})
                if after - origin >= args.seconds:
                    break
                deadline += .002
                if deadline <= after:
                    deadline = after + .002  # Skip missed deadlines; never spin to catch up.
                time.sleep(max(0, deadline - time.perf_counter()))
    except BaseException as error:
        failure = error
    finally:
        stop_event.set()
        start_event.set()
        if probe:
            probe.close()
        for process in processes:
            process.join(timeout=8)
            if process.is_alive():
                # Only the sampler's own worker; never terminate the target game.
                process.terminate()
                process.join(timeout=2)
                failure = failure or RuntimeError('Sampler worker did not exit cleanly')
        with (output / 'frames.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['read_start_s', 'read_end_s', 'frame', 'completed'])
            writer.writeheader()
            writer.writerows(polls)
        summary = {**identity, **summarize_frames(polls), 'requested_duration_s': args.seconds,
                   'requested_poll_period_ms': 2, 'gpu_requested': args.gpu}
        for kind in (['cpu', 'gpu'] if args.gpu else ['cpu']):
            path = output / f'{kind}.json'
            if path.exists():
                data = json.loads(path.read_text())
                if data.get('error'):
                    failure = failure or RuntimeError(f"{kind} worker failed: {data['error']}")
                if kind == 'cpu':
                    for key in ('cpu_cores', 'cpu_interval_s', 'guest_before', 'guest_after'):
                        if key in data:
                            summary[key] = data[key]
            else:
                failure = failure or RuntimeError(f'Missing {kind} worker output')
        if failure:
            summary['error'] = str(failure)
        (output / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2), flush=True)
    if failure:
        raise failure
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
