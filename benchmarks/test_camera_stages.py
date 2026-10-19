import csv
from pathlib import Path
import tempfile
import unittest
from benchmarks.camera_stages import summarize


class CameraStageTests(unittest.TestCase):
    def test_preserves_exposure_origin_and_joins_readout_by_frame_number(self):
        rows = [
            ('sensor', 10_000_999, 13_000_999, 42, 0),
            ('readout', 12_000_999, 13_001_999, 42, 0),
            ('camera_buffer', 10_000_999, 17_000_999, 0, 0),
            ('encoded', 10_000_000, 19_000_999, 1, 18_000_999),
            ('encoded', 20_000_000, 29_000_999, 2, 28_000_999),  # Unverified frame.
            ('rolling_skew', 10_000_999, 20_000_999, 42, 3_000_000),
            ('pipeline_depth', 10_000_999, 20_001_999, 42, 8),
            ('trace_dropped', 0, 30_000_000, 0, 0),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            with (path / 'phone_trace.csv').open('w') as handle:
                writer = csv.writer(handle)
                writer.writerow(['event', 'pts_ns', 'time_ns', 'frame_id', 'value'])
                writer.writerows(rows)
            result = summarize(path, warmup=0)
        self.assertEqual(result['sensor_to_encoded_ms']['p50'], 8)
        self.assertEqual(result['sensor_to_encoded_ms']['max'], 8)
        self.assertEqual(result['sensor_to_camera_buffer_ms']['p50'], 7)
        self.assertEqual(result['exposure_start_to_readout_start_ms']['p50'], 2)
        self.assertEqual(result['rolling_skew_ms']['p50'], 3)

    def test_rejects_incomplete_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'phone_trace.csv').write_text('event,pts_ns,time_ns,frame_id,value\nsensor,10,20,1,0\n')
            with self.assertRaises(ValueError): summarize(path)


if __name__ == '__main__':
    unittest.main()
