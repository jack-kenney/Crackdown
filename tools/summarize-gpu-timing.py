"""Summarize complete sampled guest frames from the optional D3D12 GPU profiler.

GPU category intervals are inclusive and may overlap. They are never added
together to obtain total GPU time. Physical presentations are not measured.
"""
import argparse
import collections
import csv
import json
import math
from pathlib import Path
import statistics

CATEGORIES = {
    'render_targets': ('rt_update_inclusive_gpu_ms', 'rt_scopes'),
    'textures': ('texture_inclusive_gpu_ms', 'texture_scopes'),
    'uploads': ('upload_inclusive_gpu_ms', 'upload_scopes'),
    'resolve': ('resolve_inclusive_gpu_ms', 'resolve_scopes'),
    'readback': ('readback_inclusive_gpu_ms', 'readback_scopes'),
    'gamma': ('gamma_inclusive_gpu_ms', 'gamma_scopes'),
    'fxaa': ('fxaa_inclusive_gpu_ms', 'fxaa_scopes'),
}
COUNTERS = ('recorded_guest_draws', 'resolves', 'texture_loads', 'upload_bytes')
FLAGS = ('first_in_frame', 'closes_frame', 'details', 'valid')
INTEGERS = ('sample', 'guest_frame', 'submission', *FLAGS, *COUNTERS, 'query_pairs',
            'dropped_scopes', 'forced_scope_ends', 'frame_skipped_submissions', 'skipped_submissions',
            *(fields[1] for fields in CATEGORIES.values()))
FLOATS = ('guest_submission_gpu_ms', *(fields[0] for fields in CATEGORIES.values()))
REQUIRED = set(INTEGERS + FLOATS)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metrics(values):
    return {'average': statistics.fmean(values) if values else None,
            'p50': percentile(values, .5), 'p95': percentile(values, .95)}


def frame_window(path):
    """Strictly exclude both counter endpoint IDs; timestamps are different clocks."""
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not {'frame', 'read_start_s', 'read_end_s'} <= set(reader.fieldnames or []):
            raise ValueError('--frames requires sample-renderer frames.csv with counter read windows')
        rows = []
        for raw in reader:
            try:
                frame = int(raw['frame'])
                before, after = float(raw['read_start_s']), float(raw['read_end_s'])
            except (TypeError, ValueError) as error:
                raise ValueError('Malformed frame counter read window') from error
            if frame < 0 or not math.isfinite(before) or not math.isfinite(after) or before > after:
                raise ValueError('Invalid frame counter read window')
            if rows and (frame < rows[-1][0] or before < rows[-1][1]):
                raise ValueError('Frame sampling counter or clock regressed; cannot select a reliable window')
            rows.append((frame, before, after))
    if not rows:
        raise ValueError('Frame sampling window is empty')
    return {'first_frame': rows[0][0], 'last_frame': rows[-1][0],
            'read_start_s': rows[0][1], 'read_end_s': rows[-1][2],
            'strict_endpoint_exclusion': True}


def read_rows(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError('GPU profiler CSV lacks required schema fields: ' + ', '.join(sorted(missing)))
        rows, malformed = [], []
        for line, raw in enumerate(reader, 2):
            row = {'line': line}
            try:
                for key in INTEGERS:
                    row[key] = int(raw[key])
                    if row[key] < 0:
                        raise ValueError(f'{key} is negative')
                for key in FLOATS:
                    row[key] = float(raw[key])
                    if not math.isfinite(row[key]) or row[key] < 0:
                        raise ValueError(f'{key} is not a finite nonnegative duration')
                if any(row[key] not in (0, 1) for key in FLAGS):
                    raise ValueError('Flag fields must be 0 or 1')
                if not 1 <= row['query_pairs'] <= 512:
                    raise ValueError('Query pair count outside the profiler budget')
            except (KeyError, TypeError, ValueError) as error:
                row['parse_error'] = str(error)
                # Keep malformed rows with valid grouping IDs so the frame is
                # rejected rather than quietly summarizing its other pieces.
                try:
                    row.update({key: int(raw[key]) for key in ('sample', 'guest_frame', 'submission')})
                    if any(row[key] < 0 for key in ('sample', 'guest_frame', 'submission')):
                        raise ValueError('Negative grouping ID')
                except (KeyError, TypeError, ValueError):
                    malformed.append({'line': line, 'reason': str(error)})
                    continue
            rows.append(row)
    return rows, malformed


def summarize(rows, malformed=(), window=None):
    rows = sorted(rows, key=lambda row: (row['guest_frame'], row['sample'], row['submission'], row['line']))
    groups = collections.defaultdict(list)
    by_submission = collections.Counter(row['submission'] for row in rows)
    frame_samples, sample_frames = collections.defaultdict(set), collections.defaultdict(set)
    for row in rows:
        key = row['guest_frame'], row['sample']
        groups[key].append(row)
        frame_samples[key[0]].add(key[1])
        sample_frames[key[1]].add(key[0])
    whole_frames, detail_frames = [], []
    rejected_whole, rejected_detail, excluded = [], [], []
    for (frame, sample), group in groups.items():
        identity = {'guest_frame': frame, 'sample': sample}
        if window and not window['first_frame'] < frame < window['last_frame']:
            excluded.append(identity)
            continue
        reasons = []
        if malformed:
            # An unidentifiable corrupt row could duplicate or remove a piece
            # of any frame. Do not infer completeness from the remaining rows.
            reasons.append('unassigned_malformed_csv_row')
        if any('parse_error' in row for row in group):
            reasons.append('malformed_row')
        if any(by_submission[row['submission']] != 1 for row in group):
            reasons.append('duplicate_submission')
        if len(frame_samples[frame]) != 1 or len(sample_frames[sample]) != 1:
            reasons.append('inconsistent_frame_sample_identity')
        if not reasons:
            if any(not row['valid'] for row in group):
                reasons.append('invalid_gpu_result')
            if sum(row['first_in_frame'] for row in group) != 1 or not group[0]['first_in_frame']:
                reasons.append('missing_or_misplaced_first_submission')
            if sum(row['closes_frame'] for row in group) != 1 or not group[-1]['closes_frame']:
                reasons.append('missing_or_misplaced_closing_submission')
            if any(after['submission'] != before['submission'] + 1 for before, after in zip(group, group[1:])):
                reasons.append('missing_submission')
            if any(row['frame_skipped_submissions'] for row in group):
                reasons.append('skipped_submission_in_frame')
            if len({row['details'] for row in group}) != 1:
                reasons.append('inconsistent_detail_mode')
        if reasons:
            rejected_whole.append({**identity, 'reasons': reasons})
            continue
        result = {**identity, 'submissions': len(group),
                  'first_submission': group[0]['submission'], 'last_submission': group[-1]['submission'],
                  'guest_submission_gpu_ms': sum(row['guest_submission_gpu_ms'] for row in group),
                  **{counter: sum(row[counter] for row in group) for counter in COUNTERS}}
        whole_frames.append(result)
        detail_reasons = []
        if not group[0]['details']:
            detail_reasons.append('details_disabled')
        if any(row['dropped_scopes'] for row in group):
            detail_reasons.append('query_scope_budget_exhausted')
        if any(row['forced_scope_ends'] for row in group):
            detail_reasons.append('scope_truncated_at_submission_boundary')
        if detail_reasons:
            rejected_detail.append({**identity, 'reasons': detail_reasons})
        else:
            detail_frames.append({**result, 'inclusive_categories': {
                category: {'gpu_ms': sum(row[fields[0]] for row in group),
                           'scopes': sum(row[fields[1]] for row in group)}
                for category, fields in CATEGORIES.items()}})
    return {
        'rows': len(rows), 'groups': len(groups), 'frame_window': window,
        'complete_frame_samples': len(whole_frames), 'complete_detail_frame_samples': len(detail_frames),
        'guest_frame_gpu_ms': metrics([frame['guest_submission_gpu_ms'] for frame in whole_frames]),
        'average_per_complete_frame': {
            counter: statistics.fmean(frame[counter] for frame in whole_frames) if whole_frames else None
            for counter in ('submissions', *COUNTERS)},
        'inclusive_categories': {
            category: {'gpu_ms': metrics([frame['inclusive_categories'][category]['gpu_ms'] for frame in detail_frames]),
                       'average_scopes': statistics.fmean(frame['inclusive_categories'][category]['scopes'] for frame in detail_frames) if detail_frames else None}
            for category in CATEGORIES},
        'frames': whole_frames, 'detail_frames': detail_frames,
        'rejected_frames': rejected_whole, 'rejected_detail_frames': rejected_detail,
        'excluded_window_frames': excluded, 'malformed_rows_without_frame_identity': list(malformed),
        'maximum_cumulative_skipped_submissions': max((row.get('skipped_submissions', 0) for row in rows), default=0),
        'notes': [
            'Total per-frame GPU time is the sum of completed guest submission timestamp intervals; presenter work is excluded.',
            'Inclusive category intervals can overlap and must not be added to obtain total GPU time.',
            'Dropped or forced-closed category scopes exclude detail attribution while preserving valid whole-frame timings.',
            'An optional frame window must come from the same game process/run; GPU CSV has no process identity or CPU clock correlation.',
            'Percentiles describe complete sampled frames only; invalid, partial and skipped samples are reported explicitly.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--frames', type=Path, help='Matching sample-renderer frames.csv; both endpoint frame IDs are excluded')
    parser.add_argument('--output', type=Path, help='JSON output; otherwise print to stdout')
    args = parser.parse_args()
    try:
        rows, malformed = read_rows(args.input)
        window = frame_window(args.frames) if args.frames else None
        result = summarize(rows, malformed, window)
        result['input'] = str(args.input.resolve())
        text = json.dumps(result, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + '\n')
        else:
            print(text)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
