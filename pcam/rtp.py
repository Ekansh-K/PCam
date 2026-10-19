"""RFC 6184 H.264 receiver. A bounded frame window never feeds damaged dependency chains."""
from collections import OrderedDict
from dataclasses import dataclass, field
import struct

@dataclass
class AccessUnit:
    frame_id: int
    pts_ns: int
    encoded_ns: int
    send_ns: int
    received_ns: int
    data: bytes
    keyframe: bool

@dataclass
class Pending:
    first_ns: int
    count: int
    pts: int
    encoded: int
    parts: dict = field(default_factory=dict)
    sequences: dict = field(default_factory=dict)
    send_ns: int = 0

def depayload(payloads):
    nals, fragment = [], None
    for payload in payloads:
        if not payload:
            raise ValueError("Empty H.264 RTP payload")
        kind = payload[0] & 31
        if 1 <= kind <= 23:
            if fragment is not None:
                raise ValueError("Unterminated FU-A")
            nals.append(payload)
        elif kind == 28 and len(payload) >= 3:
            header = payload[1]
            if header & 0x80:
                if fragment is not None or header & 0x40:
                    raise ValueError("Invalid FU-A start")
                fragment = bytearray([(payload[0] & 0xe0) | (header & 31)])
            if fragment is None:
                raise ValueError("Missing FU-A start")
            fragment.extend(payload[2:])
            if header & 0x40:
                nals.append(bytes(fragment)); fragment = None
        elif kind == 24:  # STAP-A is accepted for interoperability.
            position = 1
            while position + 2 <= len(payload):
                size, = struct.unpack_from(">H", payload, position); position += 2
                if not size or position + size > len(payload): raise ValueError("Invalid STAP-A")
                nals.append(payload[position:position + size]); position += size
            if position != len(payload): raise ValueError("Truncated STAP-A")
        else:
            raise ValueError("Unsupported H.264 RTP payload")
    if fragment is not None: raise ValueError("Unterminated FU-A")
    return b"".join(b"\x00\x00\x00\x01" + nal for nal in nals), any(nal[0] & 31 == 5 for nal in nals)

class Receiver:
    def __init__(self, deadline_ms=8, gray=False):
        self.gray = gray
        self.deadline_ns = int(deadline_ms * 1e6)
        self.pending = OrderedDict()
        self.ssrc = None
        self.last_frame = -1
        self.last_encoded_ns = -1
        self.awaiting_idr = True
        self.lost_frames = 0
        self.malformed_packets = 0
        self.duplicate_packets = 0
        self.recovery_drops = 0
        self.late_packets = 0
        self.max_pending = 0
        self.request_idr = False

    def loss(self):
        self.lost_frames += 1
        self.awaiting_idr = not self.gray
        self.request_idr = not self.gray

    def expire(self, now):
        for key, frame in list(self.pending.items()):
            if now - frame.first_ns > self.deadline_ns:
                del self.pending[key]; self.loss()

    def push(self, packet, now):
        try:
            if len(packet) < 49 or packet[0] != 0x90 or packet[1] & 127 != (97 if self.gray else 96):
                raise ValueError("Invalid RTP header")
            seq, timestamp, ssrc, profile, words = struct.unpack_from(">HIIHH", packet, 2)
            if profile != 0x5043 or words != 8: raise ValueError("Invalid timestamp extension")
            frame_id, pts, encoded, sent, index, count = struct.unpack_from(">IQQQHH", packet, 16)
            if not 0 < count <= 16384 or index >= count: raise ValueError("Invalid packet index")
            if timestamp != (pts // 1000 * 90 // 1000) & 0xffffffff: raise ValueError("Inconsistent RTP timestamp")
            if bool(packet[1] & 128) != (index == count - 1): raise ValueError("Invalid RTP marker")
            if self.ssrc is None: self.ssrc = ssrc
            if self.ssrc != ssrc:
                # The authenticated phone can stop/start while the receiver
                # stays open. A new stream starts at ID 0 with a newer clock.
                if frame_id != 0 or encoded <= self.last_encoded_ns: return None
                self.pending.clear(); self.last_frame = -1
                self.awaiting_idr = True; self.ssrc = ssrc
            self.expire(now)
            if frame_id <= self.last_frame:
                self.late_packets += 1; return None
            if frame_id not in self.pending:
                # Only one incomplete frame plus one next frame is retained.
                while len(self.pending) >= 2:
                    self.pending.popitem(last=False); self.loss()
                self.pending[frame_id] = Pending(now, count, pts, encoded)
                self.max_pending = max(self.max_pending, len(self.pending))
            frame = self.pending[frame_id]
            if (frame.count, frame.pts, frame.encoded) != (count, pts, encoded): raise ValueError("Inconsistent frame metadata")
            if index in frame.parts:
                self.duplicate_packets += 1; return None
            frame.parts[index] = packet[48:]; frame.sequences[index] = seq
            frame.send_ns = max(frame.send_ns, sent)
            if len(frame.parts) != count: return None
            for position in range(1, count):
                if frame.sequences[position] != (frame.sequences[position - 1] + 1) & 65535:
                    del self.pending[frame_id]; self.loss(); return None
            # A completed newer frame makes an incomplete older dependency unusable.
            for older in list(self.pending):
                if older < frame_id: del self.pending[older]; self.loss()
            del self.pending[frame_id]
            if self.last_frame >= 0 and frame_id != self.last_frame + 1: self.loss()
            self.last_frame = frame_id
            self.last_encoded_ns = encoded
            payloads = [frame.parts[i] for i in range(count)]
            data, keyframe = (b''.join(payloads), True) if self.gray else depayload(payloads)
            if self.gray:
                if len(data) < 8 or data[:4] != b'GRAY': raise ValueError('Invalid grayscale header')
                width, height = struct.unpack_from('>HH', data, 4)
                if width < 2 or height < 2 or width > 2048 or height > 4096 or width % 2 or height % 2 or len(data) != 8 + width * height:
                    raise ValueError('Invalid grayscale dimensions or frame size')
            if keyframe: self.awaiting_idr = False
            if self.awaiting_idr:
                self.recovery_drops += 1; self.request_idr = True; return None
            return AccessUnit(frame_id, pts, encoded, frame.send_ns, now, data, keyframe)
        except (ValueError, struct.error):
            self.malformed_packets += 1
            self.awaiting_idr = not self.gray; self.request_idr = not self.gray
            return None
