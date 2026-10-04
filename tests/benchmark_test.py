"""Check route safety, replay timing, and honest frame summaries without a game."""
import copy
import sys
import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from benchmark_game import NEUTRAL, execute, route_state, summarize, validate_route


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.route = {'schema': 1, 'duration_s': 3, 'warmup_s': .5, 'samples': [
            {'t': 0, 'pad': dict(NEUTRAL)},
            {'t': 1, 'pad': {**NEUTRAL, 'rt': 255}},
            {'t': 2, 'pad': dict(NEUTRAL)}]}

    def test_replay_uses_latest_state_and_releases_at_end(self):
        validate_route(self.route)
        self.assertEqual(route_state(self.route, [0, 1, 2], .999)['rt'], 0)
        self.assertEqual(route_state(self.route, [0, 1, 2], 1)['rt'], 255)
        self.assertEqual(route_state(self.route, [0, 1, 2], 1.9)['rt'], 255)
        self.assertEqual(route_state(self.route, [0, 1, 2], 2), NEUTRAL)
        self.assertEqual(route_state(self.route, [0, 1, 2], 3), NEUTRAL)

    def test_bad_or_nonfinite_route_rejected_before_input(self):
        mutations = [lambda r: r.update(duration_s=float('nan')),
                     lambda r: r.update(warmup_s=3),
                     lambda r: r['samples'][0].update(t=.1),
                     lambda r: r['samples'][1].update(t=0),
                     lambda r: r['samples'][1].update(t=float('inf')),
                     lambda r: r['samples'][1].update(t=3),
                     lambda r: r['samples'][1]['pad'].update(rt=256),
                     lambda r: r['samples'][1]['pad'].update(lx=-32769),
                     lambda r: r['samples'][1]['pad'].update(buttons=True),
                     lambda r: r['samples'][1]['pad'].pop('ry')]
        for mutate in mutations:
            route = copy.deepcopy(self.route)
            mutate(route)
            with self.assertRaises(ValueError):
                validate_route(route)

    def test_first_and_last_partial_intervals_excluded(self):
        rows = [dict(t=t, frame=f) for t, f in [(0, 10), (.01, 11), (.05, 12), (.15, 13), (.2, 13)]]
        report = summarize(rows)
        self.assertEqual(report['guest_submission_fps'], 15)
        self.assertEqual(report['observed_intervals'], 2)
        self.assertAlmostEqual(report['frame_ms_p95'], 97)
        self.assertEqual(report['intervals_over_50ms'], 1)
        self.assertAlmostEqual(report['trailing_no_submission_ms'], 50)

    def test_missed_polls_not_reported_as_individual_frame_times(self):
        report = summarize([dict(t=t, frame=f) for t, f in [(0, 0), (.03, 1), (.1, 3), (.14, 4), (.2, 4)]])
        self.assertEqual(report['frames_not_individually_observed'], 1)
        self.assertEqual(report['observed_intervals'], 1)
        self.assertAlmostEqual(report['frame_ms_p50'], 40)

    def test_frozen_game_is_zero_fps_with_visible_stall(self):
        report = summarize([dict(t=0, frame=10), dict(t=5, frame=10)])
        self.assertEqual(report['guest_submission_fps'], 0)
        self.assertIsNone(report['frame_ms_p99'])
        self.assertEqual(report['longest_observed_submission_gap_ms'], 5000)

    def test_interruption_releases_controls_and_preserves_partial_recording(self):
        clock = [0]
        def tick():
            clock[0] += .001
            return clock[0]
        controller = Mock()
        controller.read_int.return_value = 0
        physical = Mock()
        physical.read.return_value = dict(NEUTRAL)
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(mode='record', pid=123, bridge=True, controller=0,
                warmup=0, seconds=1, countdown=0, note='test', offsets=Path('unused'),
                route=Path(directory) / 'route.json', output=Path(directory) / 'result')
            probe = Mock()
            probe.read.return_value = (10, 8)
            probe.cpu_seconds.return_value = 1
            probe.identity = {}
            with patch('benchmark_game.Controller', return_value=controller), \
                 patch('benchmark_game.FrameProbe', return_value=probe), \
                 patch('benchmark_game.XInput', return_value=physical), \
                 patch('benchmark_game.time.perf_counter', side_effect=tick), \
                 patch('benchmark_game.time.sleep', side_effect=[None, KeyboardInterrupt]), \
                 patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):
                    execute(args)
            controller.state.assert_called_with(lease_ms=0)
            controller.close.assert_called_once()
            probe.close.assert_called_once()
            recording = validate_route(json.loads(args.route.read_text()))
            self.assertFalse(recording['complete'])
            self.assertFalse(json.loads((args.output / 'summary.json').read_text())['complete'])


if __name__ == '__main__':
    unittest.main()
