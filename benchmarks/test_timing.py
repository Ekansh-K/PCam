import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("latency", Path(__file__).with_name("scrcpy_latency.py"))
latency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latency)


class TimingTests(unittest.TestCase):
    def test_clock_exchange_removes_phone_processing_time(self):
        class Socket:
            def sendall(self, data):
                self.request = data
            def recv(self, size):
                return struct.pack(">qqqq", 0, 2100, 2200, 2000)
        sock = Socket()
        with patch.object(latency.time, "monotonic_ns", side_effect=[1000, 1400]):
            sample, = latency.clock_samples(sock, count=1)
        # 100 ns of phone processing leaves 300 ns network RTT, not 400 ns.
        self.assertEqual(sample["uncertainty_ns"], 150)
        self.assertEqual(sample["offset_ns"], 950)
        self.assertEqual((sample["lower_ns"], sample["upper_ns"]), (800, 1100))
        # A true offset of 1000 is inside the interval even with asymmetric paths.
        self.assertLessEqual(sample["lower_ns"], 1000)
        self.assertGreaterEqual(sample["upper_ns"], 1000)

    def test_exact_read_handles_fragmentation_and_disconnect(self):
        class Socket:
            def __init__(self, parts):
                self.parts = iter(parts)
            def recv(self, size):
                return next(self.parts)
        self.assertEqual(latency.exact(Socket([b"a", b"bc"]), 3), b"abc")
        with self.assertRaises(EOFError):
            latency.exact(Socket([b"a", b""]), 3)

    def test_percentiles_use_entire_distribution(self):
        result = latency.percentiles([100, 0, 10])
        self.assertEqual(result["p50"], 10)
        self.assertAlmostEqual(result["p95"], 91)
        self.assertIsNone(latency.percentiles([]))


if __name__ == "__main__":
    unittest.main()
