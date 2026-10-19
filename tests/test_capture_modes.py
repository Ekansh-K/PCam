from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from pcam.live import output_size

ROOT = Path(__file__).resolve().parents[1]


class CaptureModeTests(unittest.TestCase):
    def test_phone_resolution_and_receiver_override(self):
        self.assertEqual(output_size({}), (480, 960))
        self.assertEqual(output_size({'output_width': 720, 'output_height': 1600}), (720, 1600))
        self.assertEqual(output_size({'output_width': 720, 'output_height': 1600}, [480, 960]), (480, 960))

    def test_invalid_variable_hfr_rejected_before_touching_camera_or_output(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'scratch') as temporary:
            output = Path(temporary) / 'run'
            result = subprocess.run([sys.executable, '-m', 'pcam.run', '--phone', '127.0.0.1',
                                     '--fps', '240', '--min-fps', '60', '--output', str(output)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('fixed high-speed FPS', result.stderr)
            self.assertFalse(output.exists())
