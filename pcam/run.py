"""Run and timestamp the custom Android Camera2/MediaCodec → RTP/UDP → portrait V4L2 path."""
import argparse
import csv
import ctypes as C
import json
import os
import secrets
import socket
import struct
import subprocess
import time
from pathlib import Path
from . import native
from .metrics import exact, clock_samples, clock_model, percentiles, consumer_matches
from .rtp import Receiver
from .paths import ROOT, STATE, tool

ADB = tool('adb', 'platform-tools/adb')
PACKAGE = "dev.pcam.app"

def adb(serial, *args, binary=False):
    return subprocess.check_output([str(ADB), "-s", serial, *args], text=not binary)

def read_utf(sock):
    size, = struct.unpack(">H", exact(sock, 2))
    return exact(sock, size).decode()

class Clock:
    def __init__(self, sock): self.sock = sock
    def sendall(self, data): self.sock.sendall(b"\x01" + data)
    def recv(self, size): return self.sock.recv(size)

def save_csv(path, rows):
    if not rows: return
    with path.open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

def run(args):
    output = Path(args.output); output.mkdir(parents=True, exist_ok=False)
    library = native.build()
    handle = library.decoder_open(b"h264")
    if not handle: raise RuntimeError("Cannot open CPU H.264 decoder")
    if library.decoder_set_rotation(handle, args.rotation) < 0: raise RuntimeError("Unsupported rotation")
    if args.output_size and library.decoder_set_size(handle, *args.output_size) < 0:
        raise ValueError("Output dimensions must be even and between 2 and 4096")
    if library.decoder_set_output(handle, args.device.encode(), args.fps) < 0: raise RuntimeError("Cannot open webcam output")
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); route.connect((args.phone, args.control_port))
    host = args.bind or route.getsockname()[0]; route.close()
    video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    video.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    video.bind((host, args.video_port)); video.settimeout(.1)
    session = json.loads(Path(args.session_file).read_text()) if args.session_file else None
    if session and session['phone'] != args.phone: raise ValueError("Session phone does not match --phone")
    token = session['token'] if session else secrets.token_hex(16)
    if args.install:
        print(adb(args.serial, "install", "-r", "-g", str(ROOT / "scratch/android-build/pcam.apk")), flush=True)
    if not session: adb(args.serial, "shell", "am", "force-stop", PACKAGE)
    command = ["shell", "am", "start", "-n", PACKAGE + "/.MainActivity", "--es", "host", host,
               "--es", "token", token, "--es", "camera", args.camera,
               "--ei", "width", str(args.width), "--ei", "height", str(args.height),
               "--ei", "fps", str(args.fps), "--ei", "bitrate", str(args.bitrate),
               "--ei", "min_fps", str(args.min_fps or args.fps),
               "--ei", "video_port", str(args.video_port), "--ei", "control_port", str(args.control_port),
               "--ez", "high_speed", str(args.high_speed).lower(), "--el", "exposure_ns", str(args.exposure_ns),
               "--ez", "vendor_low_latency", str(args.vendor_low_latency).lower(),
               "--ez", "trace_enabled", "true",
               "--ei", "trial_pcr", str(args.trial_pcr),
               "--ez", "trial_raw_stream", str(args.trial_raw_stream).lower(),
               "--ez", "trial_raw_uncompressed", str(args.trial_raw_uncompressed).lower(),
               "--ez", "trial_raw_native", str(args.trial_raw_native).lower(),
               "--ei", "sensitivity", str(args.sensitivity),
               "--ei", "raw_content_height", str(args.raw_content_height),
               "--ei", "trial_encoder_fps", str(args.trial_encoder_fps or args.fps),
               "--ez", "trial_unadvertised_fps", str(args.trial_unadvertised_fps).lower(),
               "--ez", "fast_processing", str(not args.default_processing).lower()]
    if not session: adb(args.serial, *command)
    control = None
    for _ in range(100):
        try: control = socket.create_connection((args.phone, args.control_port), timeout=1); break
        except OSError: time.sleep(.1)
    if control is None: raise RuntimeError("Phone control server did not start")
    control.settimeout(10); control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    control.sendall(struct.pack(">H", len(token)) + token.encode())
    if read_utf(control) != "PCAM/1": raise RuntimeError("Unexpected phone protocol")
    capabilities = json.loads(read_utf(control))
    if capabilities.get("timestamp_source") != 1: raise RuntimeError("Phone sensor clock is not REALTIME")
    if session:
        if (capabilities['width'], capabilities['height'], capabilities['requested_fps']) != (args.width, args.height, args.fps):
            raise RuntimeError("Existing phone camera mode does not match the benchmark settings")
        control.sendall(b"\x06\x01"); exact(control, 1)
    sync = [clock_samples(Clock(control), 60)]
    receiver = Receiver(args.deadline_ms, gray=args.trial_raw_uncompressed)
    frames, units = [], {}
    consumer, consumer_file, consumer_error = None, None, None
    decoded = (native.Frame * 16)()
    start = time.monotonic_ns(); deadline = start + int(args.seconds * 1e9)
    last_idr_request = 0
    error = None
    try:
        control.sendall(b"\x05")
        print(f"Receiving RTP/UDP on {host}:{args.video_port}; capture {args.width}x{args.height}@{args.fps}", flush=True)
        while time.monotonic_ns() < deadline:
            try: packet, source = video.recvfrom(2048)
            except socket.timeout:
                receiver.expire(time.monotonic_ns())
                if time.monotonic_ns() - start > 10_000_000_000 and not frames:
                    raise RuntimeError("No decoded frames in 10 seconds; inspect phone logs")
                continue
            now = time.monotonic_ns()
            if source[0] != args.phone: continue
            unit = receiver.push(packet, now)
            if receiver.request_idr and now - last_idr_request > 100_000_000:
                control.sendall(b"\x02"); receiver.request_idr = False; last_idr_request = now
            if unit is None: continue
            pts_us = unit.pts_ns // 1000
            units[pts_us] = unit
            if args.trial_raw_uncompressed:
                w, h = struct.unpack_from('>HH', unit.data, 4)
                count = library.decoder_feed_gray(handle, unit.data[8:], len(unit.data) - 8, pts_us, w, h, decoded)
            else:
                count = library.decoder_feed(handle, unit.data, len(unit.data), pts_us, decoded, len(decoded))
            if count < 0: raise RuntimeError(f"Decode/webcam output failed: {count}")
            for frame in decoded[:count]:
                meta = units.pop(frame.pts_us)
                frames.append(dict(pts_us=frame.pts_us, pts_ns=meta.pts_ns, frame_id=meta.frame_id,
                                   encoded_ns=meta.encoded_ns, send_ns=meta.send_ns, received_ns=meta.received_ns,
                                   ready_ns=frame.ready_ns, converted_ns=frame.converted_ns,
                                   write_start_ns=frame.write_start_ns, write_done_ns=frame.write_done_ns,
                                   width=frame.width, height=frame.height, fingerprint=frame.fingerprint))
            if frames and consumer is None:
                consumer_file = (output / "consumer.csv").open("w")
                consumer_error = (output / "consumer.log").open("w")
                consumer = subprocess.Popen([str(native.probe_path()), args.device], stdout=consumer_file, stderr=consumer_error)
                # REQBUFS changes the loopback ring. Wait for the probe's flushed
                # header after STREAMON before writing another frame into it.
                attached_by = time.monotonic() + 3
                while os.fstat(consumer_file.fileno()).st_size == 0:
                    if consumer.poll() is not None or time.monotonic() > attached_by:
                        raise RuntimeError("Webcam consumer did not finish attaching")
                    time.sleep(.001)
            if consumer is not None and consumer.poll() is not None: raise RuntimeError("Webcam consumer exited")
        sync.append(clock_samples(Clock(control), 60))
        control.sendall(b"\x04"); capabilities = json.loads(read_utf(control))
    except Exception as caught:
        error = caught
    finally:
        try:
            control.sendall(b"\x03"); exact(control, 1)
        except (OSError, EOFError): pass
        if consumer is not None:
            consumer.terminate()
            try: consumer.wait(timeout=3)
            except subprocess.TimeoutExpired: consumer.kill(); consumer.wait()
        if consumer_file: consumer_file.close()
        if consumer_error: consumer_error.close()
        if args.snapshot and frames:
            size = C.c_int()
            pixels = library.decoder_output_pixels(handle, C.byref(size))
            # Save after timing has ended, without attaching another V4L2 consumer.
            data = C.string_at(pixels, size.value)
            frame = frames[-1]
            (output / 'snapshot.pgm').write_bytes(f'P5\n{frame["width"]} {frame["height"]}\n255\n'.encode() + data[::2])
        library.decoder_close(handle); video.close(); control.close()
        # Wait for asynchronous camera/encoder cleanup and the trace writer footer.
        trace_bytes = b""
        for _ in range(30):
            trace_bytes = adb(args.serial, "exec-out", "run-as", PACKAGE, "cat", "files/trace.csv", binary=True)
            if b"trace_dropped," in trace_bytes: break
            time.sleep(.1)
        (output / "phone_trace.csv").write_bytes(trace_bytes)
        (output / "phone_status.json").write_text(json.dumps(capabilities, indent=2) + "\n")
        pid = adb(args.serial, "shell", "pidof", PACKAGE).strip()
        if pid:
            (output / "phone.log").write_text(adb(args.serial, "logcat", "-d", "--pid=" + pid, "-t", "2000"))
    save_csv(output / "frames.csv", frames)
    (output / "clock_samples.json").write_text(json.dumps(sync, indent=2))
    if error: raise error
    if len(frames) < 2: raise RuntimeError("Not enough frames")
    offset, best = clock_model(sync)
    with (output / "phone_trace.csv").open() as stream: trace = list(csv.DictReader(stream))
    if not trace or trace[-1]["event"] != "trace_dropped" or int(trace[-1]["value"]):
        raise RuntimeError("Phone trace did not finish cleanly")
    sensors = {int(row["pts_ns"]) // 1000: int(row["pts_ns"]) for row in trace if row["event"] == "sensor"}
    exposure = {int(row["pts_ns"]) // 1000: int(row["value"]) for row in trace if row["event"] == "exposure"}
    matched = sum(row["pts_us"] in sensors and -1000 < row["pts_ns"] - sensors[row["pts_us"]] <= 0 for row in frames)
    if matched < 30: raise RuntimeError(f"Not enough directly verified sensor timestamps: {matched}")
    if not args.high_speed and matched < .95 * len(frames):
        raise RuntimeError("Normal-mode sensor/encoder timestamp matching below 95%")
    for row in frames:
        row["sensor_to_encoder_ms"] = (row["encoded_ns"] - row["pts_ns"]) / 1e6
        row["encoder_to_receive_ms"] = (row["received_ns"] + offset(row["received_ns"]) - row["encoded_ns"]) / 1e6
        row["send_to_receive_ms"] = (row["received_ns"] + offset(row["received_ns"]) - row["send_ns"]) / 1e6
        row["pts_to_decoded_ms"] = (row["ready_ns"] + offset(row["ready_ns"]) - row["pts_ns"]) / 1e6
        row["conversion_rotation_ms"] = (row["converted_ns"] - row["ready_ns"]) / 1e6
        row["pts_to_v4l2_ms"] = (row["write_done_ns"] + offset(row["write_done_ns"]) - row["pts_ns"]) / 1e6
    with (output / "consumer.csv").open() as stream: consumed = list(csv.DictReader(stream))
    matches = consumer_matches(frames, consumed, offset)
    output_stable = [row for row in frames if row["ready_ns"] > frames[0]["ready_ns"] + int(args.warmup * 1e9)]
    # High-speed Camera2 callbacks are batched on this phone. Report primary
    # sensor latency only for frames whose actual sensor timestamp was observed.
    stable = [row for row in output_stable if row["pts_us"] in sensors]
    if len(stable) < 2: raise RuntimeError("Not enough frames after warmup")
    if args.output_size and any((row['width'], row['height']) != tuple(args.output_size) for row in output_stable):
        raise RuntimeError("The viewer pinned a different webcam size; close it and repeat the benchmark")
    if any(row["width"] >= row["height"] for row in stable): raise RuntimeError("Webcam output is not portrait")
    stable_pts = {row["pts_us"] for row in stable}
    selected = [row for row in matches if row["pts_us"] in stable_pts]
    if len(selected) < .95 * len(stable): raise RuntimeError("Consumer frame matching below 95%")
    camera_path = "RAW10 green-channel downsample to monochrome YUV" if args.trial_raw_stream else "Camera2 direct encoder Surface"
    coding = "uncompressed GRAY8 over custom RTP payload 97" if args.trial_raw_uncompressed else "hardware H.264, RFC6184 RTP/UDP"
    summary = dict(configuration=vars(args), implementation=f"custom Android app, {camera_path}, {coding}, CPU rotation, V4L2",
                   measurement="sensor capture timestamp to independent V4L2 consumer dequeue; excludes application rendering",
                   video_transport="RTP/UDP over LAN; no ADB video transport", clock_transport="direct TCP over LAN",
                   clock_uncertainty_ms=[sample["uncertainty_ns"] / 1e6 for sample in best],
                   clock_offset_change_ms=(best[1]["offset_ns"] - best[0]["offset_ns"]) / 1e6,
                   decoded_frames=len(frames), analyzed_frames=len(stable), sensor_pts_matched_frames=matched,
                   sensor_verification_fraction=matched / len(frames),
                   sensor_sampling="Primary latency statistics use only directly matched capture-start sensor timestamps; encoded output FPS includes every decoded frame",
                   consumer_matched_stable_frames=len(selected), width=stable[0]["width"], height=stable[0]["height"],
                   decoded_fps=(len(output_stable) - 1) * 1e9 / (output_stable[-1]["ready_ns"] - output_stable[0]["ready_ns"]),
                   loss_events=receiver.lost_frames, recovery_drops=receiver.recovery_drops, malformed_packets=receiver.malformed_packets,
                   duplicate_packets=receiver.duplicate_packets, max_incomplete_frames=receiver.max_pending,
                   phone_queue_drops=sum(row["event"].startswith("drop_") and frames[0]["pts_ns"] <= int(row["pts_ns"]) <= frames[-1]["pts_ns"] for row in trace))
    if session and capabilities.get('wifi_direct', {}).get('interface'):
        summary['video_transport'] = 'RTP/UDP over Wi-Fi Direct; no ADB video transport'
        summary['clock_transport'] = 'direct TCP over Wi-Fi Direct'
    produced = 'payload' if args.trial_raw_uncompressed else 'encoded'
    summary["encoded_frames"] = sum(row["event"] == produced and frames[0]["pts_ns"] <= int(row["pts_ns"]) <= frames[-1]["pts_ns"] for row in trace)
    summary["frame_delivery_fraction"] = len(frames) / summary["encoded_frames"]
    stable_encoded = sum(row["event"] == produced and output_stable[0]["pts_ns"] <= int(row["pts_ns"]) <= output_stable[-1]["pts_ns"] for row in trace)
    summary["steady_state_frame_delivery_fraction"] = len(output_stable) / stable_encoded
    summary["delivered_frame_intervals_ms"] = percentiles([(b["ready_ns"] - a["ready_ns"]) / 1e6 for a, b in zip(output_stable, output_stable[1:])])
    for key in ("sensor_to_encoder_ms", "encoder_to_receive_ms", "send_to_receive_ms", "pts_to_decoded_ms", "conversion_rotation_ms", "pts_to_v4l2_ms"):
        summary[key] = percentiles([row[key] for row in stable])
    summary["pts_to_consumer_ms"] = percentiles([row["pts_to_consumer_ms"] for row in selected])
    if args.trial_raw_uncompressed:
        summary['sensor_to_payload_ms'] = summary.pop('sensor_to_encoder_ms')
        summary['payload_to_receive_ms'] = summary.pop('encoder_to_receive_ms')
        summary['produced_frames'] = summary.pop('encoded_frames')
        summary['received_frames'] = summary.pop('decoded_frames')
        summary['output_fps'] = summary.pop('decoded_fps')
        summary['sensor_sampling'] = 'Primary latency statistics use directly matched capture-start timestamps; FPS counts complete received RAW frames'
    summary["exposure_ms"] = percentiles([exposure[row["pts_us"]] / 1e6 for row in stable if row["pts_us"] in exposure])
    summary["reported_sensor_frame_duration_ms"] = percentiles([int(row["value"]) / 1e6 for row in trace if row["event"] == "frame_duration"])
    if args.trial_raw_stream:
        summary["raw_processing_ms"] = percentiles([int(row["value"]) / 1e6 for row in trace if row["event"] == "raw_processed"])
        for event in ('raw_input_mapped', 'raw_converted'):
            summary[event + '_ms'] = percentiles([int(row['value']) / 1e6 for row in trace if row['event'] == event])
    save_csv(output / "frames.csv", frames); save_csv(output / "consumer_matches.csv", matches)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="b6d3efda", help="ADB is used only for app launch and trace retrieval")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--bind")
    parser.add_argument("--camera", default="0")
    parser.add_argument("--width", type=int, default=1280); parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=120)
    parser.add_argument("--min-fps", type=int, default=0, help="High-speed capture requires equal minimum and maximum FPS to avoid green frames")
    parser.add_argument("--high-speed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--bitrate", type=int, default=12_000_000)
    parser.add_argument("--rotation", type=int, choices=[90, 270], default=90)
    parser.add_argument("--output-size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
                        help="Measure the exact webcam output size after rotation and center crop")
    parser.add_argument("--device", default="/dev/video10")
    parser.add_argument("--video-port", type=int, default=5004); parser.add_argument("--control-port", type=int, default=27185)
    parser.add_argument("--deadline-ms", type=float, default=12)
    parser.add_argument("--exposure-ns", type=int, default=0)
    parser.add_argument("--trial-pcr", type=int, choices=[-1, 0, 1, 2], default=-1, help="Experimental HAL stream-start request count")
    parser.add_argument("--trial-raw-stream", action="store_true", help="Experimental RAW10 downsample to monochrome YUV before hardware encoding")
    parser.add_argument("--trial-raw-uncompressed", action="store_true", help="Send downsampled GRAY8 directly over UDP, bypassing video encoding")
    parser.add_argument("--trial-raw-native", action="store_true", help="Convert RAW with the native CPU loop")
    parser.add_argument("--sensitivity", type=int, default=400, help="ISO when manual exposure is enabled")
    parser.add_argument("--raw-content-height", type=int, default=3072, help="Active RAW rows inside the allocated buffer; this phone's 60 FPS RAW has 2304 active rows")
    parser.add_argument("--trial-encoder-fps", type=int, default=0, help="Encoder scheduling rate, independent of actual camera FPS and exposure timestamps")
    parser.add_argument("--snapshot", action="store_true", help="Save the final received frame for image-quality inspection after timing ends")
    parser.add_argument("--trial-unadvertised-fps", action="store_true", help="Probe normal-session FPS outside advertised ranges; verify actual frame timing")
    parser.add_argument("--default-processing", action="store_true")
    parser.add_argument("--vendor-low-latency", action="store_true")
    parser.add_argument("--seconds", type=float, default=30); parser.add_argument("--warmup", type=float, default=3)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--session-file", help="Measure an already paired phone without restarting its Wi-Fi Direct group")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.session_file and args.install: parser.error("An existing session cannot be combined with --install")
    if args.high_speed and args.min_fps not in (0, args.fps):
        parser.error("Use a fixed high-speed FPS range; variable ranges produce green frames on this phone")
    if args.trial_raw_uncompressed: args.trial_raw_stream = True; args.trial_raw_native = True
    if not 0 <= args.warmup < args.seconds: parser.error("Require 0 <= warmup < seconds")
    run(args)
