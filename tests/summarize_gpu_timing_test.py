import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'tools/summarize-gpu-timing.py'
spec = importlib.util.spec_from_file_location('summarize_gpu_timing', path)
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


def row(frame=10, sample=1, submission=100, **changes):
    result = {field: 0 for field in gpu.REQUIRED}
    result.update(line=submission, guest_frame=frame, sample=sample, submission=submission,
                  first_in_frame=1, closes_frame=1, details=1, valid=1, query_pairs=2,
                  guest_submission_gpu_ms=2, rt_update_inclusive_gpu_ms=3, rt_scopes=1,
                  recorded_guest_draws=7, upload_bytes=1024)
    result.update(changes)
    return result


class SummaryTests(unittest.TestCase):
    def test_sorted_complete_multisubmission_and_overlapping_categories(self):
        result = gpu.summarize([row(submission=101, first_in_frame=0, guest_submission_gpu_ms=4),
                                row(closes_frame=0, texture_inclusive_gpu_ms=10, texture_scopes=1)])
        self.assertEqual(result['complete_frame_samples'], 1)
        self.assertEqual(result['guest_frame_gpu_ms']['average'], 6)
        self.assertEqual(result['inclusive_categories']['render_targets']['gpu_ms']['average'], 6)
        self.assertEqual(result['inclusive_categories']['textures']['gpu_ms']['average'], 10)
        self.assertEqual(result['average_per_complete_frame']['recorded_guest_draws'], 14)
        self.assertEqual(result['average_per_complete_frame']['upload_bytes'], 2048)
        self.assertEqual(result['frames'][0]['first_submission'], 100)

    def test_partial_first_close_and_missing_middle_rejected(self):
        for rows in ([row(first_in_frame=0)], [row(closes_frame=0)],
                     [row(closes_frame=0), row(submission=102, first_in_frame=0)]):
            with self.subTest(rows=rows):
                result = gpu.summarize(rows)
                self.assertEqual(result['complete_frame_samples'], 0)
                self.assertEqual(len(result['rejected_frames']), 1)

    def test_duplicate_and_inconsistent_identity(self):
        for rows in ([row(), row()], [row(), row(sample=2, submission=101)],
                     [row(), row(frame=11, submission=101)]):
            with self.subTest(rows=rows):
                self.assertEqual(gpu.summarize(rows)['complete_frame_samples'], 0)

    def test_invalid_and_skipped_whole_frame(self):
        for changes in ({'valid': 0}, {'frame_skipped_submissions': 1}):
            result = gpu.summarize([row(**changes)])
            self.assertEqual(result['complete_frame_samples'], 0)
        # A skip in an earlier frame does not contaminate this complete frame.
        result = gpu.summarize([row(skipped_submissions=3)])
        self.assertEqual(result['complete_frame_samples'], 1)
        self.assertEqual(result['maximum_cumulative_skipped_submissions'], 3)

    def test_budget_and_forced_scope_reject_details_only(self):
        for changes in ({'dropped_scopes': 1, 'query_pairs': 512}, {'forced_scope_ends': 1}, {'details': 0}):
            result = gpu.summarize([row(**changes)])
            self.assertEqual(result['complete_frame_samples'], 1)
            self.assertEqual(result['complete_detail_frame_samples'], 0)
            self.assertIsNone(result['inclusive_categories']['uploads']['gpu_ms']['average'])

    def test_window_excludes_endpoints(self):
        rows = [row(frame=i, sample=i, submission=i) for i in (9, 10, 11, 12, 13)]
        result = gpu.summarize(rows, window={'first_frame': 10, 'last_frame': 12})
        self.assertEqual([frame['guest_frame'] for frame in result['frames']], [11])
        self.assertEqual(len(result['excluded_window_frames']), 4)

    def test_csv_bad_duration_budget_and_required_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gpu.csv'
            for changes in ({'guest_submission_gpu_ms': 'nan'}, {'query_pairs': 513},
                            {'guest_submission_gpu_ms': -1}, {'valid': 2}):
                with self.subTest(changes=changes):
                    raw = row(**changes)
                    with path.open('w', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=sorted(gpu.REQUIRED), extrasaction='ignore')
                        writer.writeheader(); writer.writerow(raw)
                    rows, malformed = gpu.read_rows(path)
                    self.assertEqual(gpu.summarize(rows, malformed)['complete_frame_samples'], 0)
            path.write_text('sample,guest_frame,submission\n1,1,1\n')
            with self.assertRaisesRegex(ValueError, 'required schema'):
                gpu.read_rows(path)

    def test_frame_window_monotonic_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frames.csv'
            path.write_text('read_start_s,read_end_s,frame,completed\n0,0.001,5,4\n1,1.001,8,7\n')
            result = gpu.frame_window(path)
            self.assertEqual((result['first_frame'], result['last_frame']), (5, 8))
            path.write_text('read_start_s,read_end_s,frame\n0,0.001,5\n1,1.001,1\n')
            with self.assertRaisesRegex(ValueError, 'regressed'):
                gpu.frame_window(path)

    def test_empty_has_no_metrics(self):
        result = gpu.summarize([])
        self.assertEqual(result['complete_frame_samples'], 0)
        self.assertIsNone(result['guest_frame_gpu_ms']['p95'])

    def test_unidentifiable_bad_row_blocks_completeness_claim(self):
        result = gpu.summarize([row()], malformed=[{'line': 5, 'reason': 'corrupt frame ID'}])
        self.assertEqual(result['complete_frame_samples'], 0)
        self.assertIn('unassigned_malformed_csv_row', result['rejected_frames'][0]['reasons'])


if __name__ == '__main__':
    unittest.main()
