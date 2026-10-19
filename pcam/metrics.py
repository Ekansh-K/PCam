"""Clock alignment and timestamp statistics shared by the baseline and custom receiver."""
from bisect import bisect_right
import struct
import time

def exact(sock, size):
    parts = bytearray()
    while len(parts) < size:
        part = sock.recv(size - len(parts))
        if not part: raise EOFError("Peer closed the stream")
        parts.extend(part)
    return bytes(parts)

def clock_samples(sock, count=40):
    samples = []
    for token in range(count):
        before = time.monotonic_ns()
        sock.sendall(struct.pack(">q", token))
        echoed, phone_rx, phone_tx, phone_mono = struct.unpack(">qqqq", exact(sock, 32))
        after = time.monotonic_ns()
        if echoed != token: raise ValueError("Clock exchange token mismatch")
        lower, upper = phone_tx - after, phone_rx - before
        samples.append(dict(local_ns=(before + after) // 2, offset_ns=(lower + upper) / 2,
                            uncertainty_ns=(upper - lower) / 2, lower_ns=lower, upper_ns=upper,
                            phone_boot_minus_mono_ns=phone_rx - phone_mono))
    return samples

def percentiles(values):
    ordered = sorted(values)
    if not ordered: return None
    def percentile(p):
        position = (len(ordered) - 1) * p
        low = int(position); high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
    return {"p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99),
            "min": ordered[0], "max": ordered[-1]}

def clock_model(groups):
    best = [min(group, key=lambda sample: sample["uncertainty_ns"]) for group in groups]
    a, b = best
    def offset(at_ns):
        ratio = (at_ns - a["local_ns"]) / (b["local_ns"] - a["local_ns"])
        return a["offset_ns"] + ratio * (b["offset_ns"] - a["offset_ns"])
    return offset, best

def consumer_matches(frames, events, offset):
    starts = [frame["write_start_ns"] for frame in frames]
    seen, matches = set(), []
    for event in events:
        kernel_ns = int(event["kernel_ns"])
        index = bisect_right(starts, kernel_ns + 999) - 1
        if index < 0 or kernel_ns > frames[index]["write_done_ns"]: continue
        frame = frames[index]
        if int(event["fingerprint"]) != frame["fingerprint"]:
            raise RuntimeError("V4L2 pixel fingerprint does not match the timestamped producer frame")
        if frame["pts_us"] in seen: continue
        seen.add(frame["pts_us"])
        dequeued_ns = int(event["dequeued_ns"])
        if dequeued_ns < kernel_ns: raise RuntimeError("Unexpected V4L2 timestamp clock")
        pts = frame.get("pts_ns", frame["pts_us"] * 1000)
        matches.append(dict(pts_us=frame["pts_us"], kernel_ns=kernel_ns, dequeued_ns=dequeued_ns,
                            pts_to_consumer_ms=(dequeued_ns + offset(dequeued_ns) - pts) / 1e6,
                            kernel_to_consumer_ms=(dequeued_ns - kernel_ns) / 1e6))
    return matches
