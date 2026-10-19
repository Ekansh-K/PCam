import csv
from pathlib import Path
import struct
import subprocess
import unittest
from pcam.rtp import Receiver, depayload
from pcam.metrics import consumer_matches

ROOT = Path(__file__).resolve().parents[1]

def packet(frame, payload, seq=0, index=0, count=1, pts=None, ssrc=123):
    pts = frame * 1_000_000_000 if pts is None else pts
    header = struct.pack(">BBHIIHHIQQQHH", 0x90, 96 | (128 if index == count - 1 else 0), seq,
                         pts // 1000 * 90 // 1000 & 0xffffffff, ssrc, 0x5043, 8, frame, pts, pts + 100,
                         pts + 200, index, count)
    return header + payload

class RtpTests(unittest.TestCase):
    def test_java_gray_packetization_and_independent_recovery(self):
        build = ROOT / 'scratch/test-gray-packets'; build.mkdir(exist_ok=True)
        subprocess.run(['javac', '--release', '8', '-d', str(build), str(ROOT / 'android/src/dev/pcam/app/H264Rtp.java'),
                        str(ROOT / 'tests/GrayPacketFixture.java')], check=True)
        path = build / 'packets.bin'
        subprocess.run(['java', '-cp', str(build), 'GrayPacketFixture', str(path)], check=True)
        raw = path.read_bytes(); packets = []
        while raw:
            size, = struct.unpack_from('>I', raw); packets.append(raw[4:4+size]); raw = raw[4+size:]
        receiver = Receiver(gray=True); result = None
        for index, value in enumerate(reversed(packets)):
            self.assertLessEqual(len(value), 1200)
            result = receiver.push(value, index * 1000) or result
        self.assertEqual(result.data, b'GRAY' + struct.pack('>HH', 320, 180) + bytes(i & 255 for i in range(8, 8 + 320*180)))
        self.assertEqual(result.pts_ns, 50_000_000_000_000)
        def gray(frame, index=0, count=1):
            data = bytearray(packet(frame, b'GRAY' + struct.pack('>HH', 2, 2) + b'1234', index=index, count=count, pts=50_000_000_000_000 + frame * 1000000, ssrc=42))
            data[1] = 97 | (128 if index == count-1 else 0)
            return bytes(data)
        self.assertIsNone(receiver.push(gray(1, count=2), 100000))
        self.assertIsNotNone(receiver.push(gray(2), 200000))
        self.assertFalse(receiver.request_idr)
        self.assertFalse(receiver.awaiting_idr)

    def test_java_packetizer_roundtrip_fragmentation_reordering_and_sequence_wrap(self):
        build = ROOT / "scratch/test-java"; build.mkdir(exist_ok=True)
        subprocess.run(["javac", "--release", "8", "-d", str(build),
                        str(ROOT / "android/src/dev/pcam/app/H264Rtp.java"), str(ROOT / "tests/PacketizerFixture.java")], check=True)
        path = build / "fixture.bin"
        subprocess.run(["java", "-cp", str(build), "PacketizerFixture", str(path)], check=True)
        raw = path.read_bytes(); packets = []
        while raw:
            size, = struct.unpack_from(">I", raw); packets.append(raw[4:4 + size]); raw = raw[4 + size:]
        self.assertTrue(all(len(p) <= 1200 for p in packets))
        self.assertEqual([struct.unpack_from(">H", p, 2)[0] for p in packets[:3]], [65534, 65535, 0])
        receiver = Receiver(); result = None
        # A duplicate before completion must be harmless; fragments can arrive reordered.
        order = [packets[1], packets[0], packets[0], *packets[2:]]
        for index, value in enumerate(order):
            result = receiver.push(value, index * 1000) or result
        self.assertIsNotNone(result)
        self.assertTrue(result.keyframe)
        self.assertEqual(result.pts_ns, 50_000_000_000_000)
        expected = b"\0\0\0\1\x67\x42\0\x1f\0\0\0\1\x68\1\2\0\0\0\1\x65" + b"\x55" * 4999
        self.assertEqual(result.data, expected)
        self.assertEqual(receiver.duplicate_packets, 1)

    def test_loss_blocks_dependent_frames_until_idr(self):
        receiver = Receiver()
        self.assertIsNotNone(receiver.push(packet(0, b"\x65key"), 0))
        receiver.push(packet(1, b"\x7c\x81part", seq=1, count=2), 1000)
        self.assertIsNone(receiver.push(packet(2, b"\x41dependent", seq=3), 2000))
        self.assertTrue(receiver.request_idr)
        self.assertIsNotNone(receiver.push(packet(3, b"\x65recover", seq=4), 3000))
        self.assertEqual(receiver.recovery_drops, 1)

    def test_frame_window_and_deadline_are_bounded(self):
        receiver = Receiver(deadline_ms=2)
        for index in range(10):
            receiver.push(packet(index, b"\x7c\x85part", seq=index, count=2), index * 1000)
        self.assertLessEqual(len(receiver.pending), 2)
        receiver.expire(10_000_000)
        self.assertEqual(len(receiver.pending), 0)
        self.assertTrue(receiver.awaiting_idr)

    def test_phone_can_restart_without_accepting_delayed_old_stream(self):
        receiver = Receiver()
        old = packet(0, b"\x65old", pts=1_000_000)
        self.assertIsNotNone(receiver.push(old, 0))
        self.assertIsNotNone(receiver.push(packet(1, b"\x41p", pts=2_000_000), 1000))
        self.assertIsNotNone(receiver.push(packet(0, b"\x65new", pts=3_000_000, ssrc=456), 2000))
        self.assertIsNone(receiver.push(old, 3000))
        self.assertEqual(receiver.ssrc, 456)

    def test_rejects_truncated_fragment_and_malformed_marker(self):
        with self.assertRaises(ValueError): depayload([b"\x7c\x45end"])
        receiver = Receiver(); value = bytearray(packet(0, b"\x65key")); value[1] &= 127
        self.assertIsNone(receiver.push(bytes(value), 0))
        self.assertEqual(receiver.malformed_packets, 1)

    def test_consumer_timestamp_quantization_and_pixel_identity(self):
        frames = [dict(pts_us=10, write_start_ns=10050, write_done_ns=11050, fingerprint=123)]
        events = [dict(kernel_ns=10000, dequeued_ns=12000, fingerprint=123)]
        rows = consumer_matches(frames, events + events, lambda _: 1000)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pts_to_consumer_ms"], .003)
        events[0]["fingerprint"] = 456
        with self.assertRaises(RuntimeError): consumer_matches(frames, events, lambda _: 0)

if __name__ == "__main__": unittest.main()
