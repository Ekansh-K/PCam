#!/usr/bin/env python3
"""Timestamp scrcpy 5.0.1 camera packets and decoded frames. No image is saved."""
import argparse
import csv
import ctypes as C
import json
from pathlib import Path
import socket
import struct
import subprocess
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcam.metrics import exact, clock_samples, percentiles, clock_model, consumer_matches as match_consumer
from pcam.native import Frame

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT / "scratch/tools/platform-tools/adb"
SERVER = ROOT / "scratch/tools/scrcpy-linux-x86_64-v5.0.1/scrcpy-server"
SCRATCH = ROOT / "scratch"


def adb(serial, *args):
    return subprocess.check_output([str(ADB), "-s", serial, *args], text=True).strip()




def connect(port, process, clock=False):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Android process exited; inspect its log")
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            sock.settimeout(15)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # adb accepts locally before its remote listener is necessarily ready.
            # Verify a remote response rather than treating TCP connect as readiness.
            if clock:
                sock.sendall(struct.pack(">q", -1))
                exact(sock, 32)
                return sock, None
            return sock, exact(sock, 4)
        except (ConnectionRefusedError, ConnectionResetError, EOFError):
            if "sock" in locals():
                sock.close()
            time.sleep(0.1)
    raise TimeoutError("Android listener did not start")








def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run(["g++", "-O2", "-shared", "-fPIC", str(ROOT / "benchmarks/decoder.cpp"),
                    "-I", str(SCRATCH / "tools/libyuv/usr/include"),
                    "-o", str(SCRATCH / "decoder.so"), "-lavcodec", "-lavutil", "-lswscale", "-l:libyuv.so.0"], check=True)
    if args.v4l2_device:
        subprocess.run(["g++", "-O2", str(ROOT / "benchmarks/v4l2_probe.cpp"),
                        "-o", str(SCRATCH / "v4l2-probe")], check=True)
    library = C.CDLL(str(SCRATCH / "decoder.so"))
    library.decoder_open.argtypes = [C.c_char_p]
    library.decoder_open.restype = C.c_void_p
    library.decoder_feed.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_int64,
                                     C.POINTER(Frame), C.c_int]
    library.decoder_close.argtypes = [C.c_void_p]
    library.decoder_set_output.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    decoder = library.decoder_open(args.decoder.encode())
    if not decoder:
        raise RuntimeError(f"Cannot open decoder {args.decoder}")
    # All phone and forwarding changes are tracked for cleanup in finally.
    processes, logs, forwards, sockets = [], [], [], []
    consumer = None
    trace_started = False
    frames, packets, sync = [], [], []
    device_files = ["/data/local/tmp/pcam-clock.jar", "/data/local/tmp/pcam-scrcpy.jar"]
    remote_pidfiles = ["/data/local/tmp/pcam-clock.pid", "/data/local/tmp/pcam-scrcpy.pid"]
    try:
        if args.v4l2_device:
            result = library.decoder_set_output(decoder, args.v4l2_device.encode(), args.v4l2_fps)
            if result < 0:
                raise OSError(-result, "Cannot open V4L2 output")
        server_file = SCRATCH / "instrumented-scrcpy.jar" if args.instrumented else SERVER
        if args.instrumented:
            adb(args.serial, "shell", "rm -f /data/local/tmp/pcam-trace.csv")
        for local, remote in zip([SCRATCH / "clock.jar", server_file], device_files):
            adb(args.serial, "push", str(local), remote)
        for serial, port, target in [(args.clock_serial or args.serial, 27184, "tcp:27184"),
                                     (args.serial, 27183, "localabstract:scrcpy_12345678")]:
            # Fixed benchmark ports fail if occupied; never replace another forward.
            adb(serial, "forward", "--no-rebind", f"tcp:{port}", target)
            forwards.append((serial, port))
        def launch(name, remote, main, options, pidfile):
            log = open(output / f"{name}.log", "w")
            logs.append(log)
            command = f"echo $$ > {pidfile}; CLASSPATH={remote} exec app_process / {main} {options}"
            process = subprocess.Popen([str(ADB), "-s", args.serial, "shell", command],
                                       stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process
        clock = launch("clock", device_files[0], "ClockProbe", "27184", remote_pidfiles[0])
        clock_socket, _ = connect(27184, clock, clock=True)
        sockets.append(clock_socket)
        sync.append(clock_samples(clock_socket))
        options = ("5.0.1 scid=12345678 tunnel_forward=true audio=false control=false "
                   "cleanup=false send_device_meta=false send_dummy_byte=false "
                   "video_source=camera video_codec=h264 video_encoder=c2.qti.avc.encoder "
                   f"camera_id={args.camera} camera_size={args.size} camera_fps={args.fps} "
                   f"capture_orientation={args.rotation} video_bit_rate={args.bitrate}")
        if args.high_speed:
            options += " camera_high_speed=true"
        if args.codec_options:
            options += f" video_codec_options={args.codec_options}"
        server = launch("server", device_files[1], "com.genymobile.scrcpy.Server", options, remote_pidfiles[1])
        trace_started = True
        stream, codec = connect(27183, server)
        sockets.append(stream)
        if codec != b"h264":
            raise ValueError(f"Unexpected codec header {codec!r}")
        start = time.monotonic_ns()
        deadline = start + int(args.seconds * 1e9)
        config = b""
        decoded = (Frame * 16)()
        while time.monotonic_ns() < deadline:
            header = exact(stream, 12)
            raw_pts, size = struct.unpack(">QI", header)
            if raw_pts & (1 << 63):
                continue  # scrcpy 5 session metadata: flags, width, height
            if not 0 < size <= 32 * 1024 * 1024:
                raise ValueError(f"Invalid packet size {size}")
            data = exact(stream, size)
            received_ns = time.monotonic_ns()
            if raw_pts & (1 << 62):
                config = data
                continue
            pts_us = raw_pts & ((1 << 61) - 1)
            packets.append(dict(pts_us=pts_us, received_ns=received_ns, bytes=size))
            data = config + data
            config = b""
            decode_start = time.monotonic_ns()
            count = library.decoder_feed(decoder, data, len(data), pts_us, decoded, len(decoded))
            if count < 0:
                raise RuntimeError(f"FFmpeg decode failed: {count}")
            for frame in decoded[:count]:
                row = dict(pts_us=frame.pts_us, ready_ns=frame.ready_ns,
                           decode_call_ms=(frame.ready_ns - decode_start) / 1e6,
                           width=frame.width, height=frame.height)
                if args.v4l2_device:
                    row.update(converted_ns=frame.converted_ns, write_start_ns=frame.write_start_ns,
                               write_done_ns=frame.write_done_ns, fingerprint=frame.fingerprint)
                frames.append(row)
            if args.v4l2_device and frames and consumer is None:
                csv_log = open(output / "consumer.csv", "w")
                error_log = open(output / "consumer.log", "w")
                logs.extend([csv_log, error_log])
                consumer = subprocess.Popen([str(SCRATCH / "v4l2-probe"), args.v4l2_device],
                                            stdout=csv_log, stderr=error_log)
                processes.append(consumer)
            if consumer is not None and consumer.poll() is not None:
                raise RuntimeError("V4L2 consumer exited; inspect consumer.log")
            # Avoid stopping the video reader for clock probes. Sample only before/after.
        sync.append(clock_samples(clock_socket))
    finally:
        for sock in sockets:
            sock.close()
        for pidfile in remote_pidfiles:
            # Only kill a PID if its command is one of our benchmark processes.
            command = (f'if test -f {pidfile}; then p=$(cat {pidfile}); '
                       'case "$(cat /proc/$p/cmdline 2>/dev/null)" in '
                       '*ClockProbe*|*com.genymobile.scrcpy.Server*) kill "$p";; esac; '
                       f'rm -f {pidfile}; fi')
            subprocess.run([str(ADB), "-s", args.serial, "shell", command],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()
        for serial, port in forwards:
            subprocess.run([str(ADB), "-s", serial, "forward", "--remove", f"tcp:{port}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        library.decoder_close(decoder)
        if args.instrumented and trace_started:
            subprocess.run([str(ADB), "-s", args.serial, "pull", "/data/local/tmp/pcam-trace.csv",
                            str(output / "phone_trace.csv")], check=False, timeout=20)
    offset, best = clock_model(sync)
    a, b = best
    receipt = {packet["pts_us"]: packet["received_ns"] for packet in packets}
    for packet in packets:
        packet["pts_to_receive_ms"] = (packet["received_ns"] + offset(packet["received_ns"]) - packet["pts_us"] * 1000) / 1e6
    for frame in frames:
        frame["pts_to_decoded_ms"] = (frame["ready_ns"] + offset(frame["ready_ns"]) - frame["pts_us"] * 1000) / 1e6
        frame["receive_to_decoded_ms"] = (frame["ready_ns"] - receipt[frame["pts_us"]]) / 1e6
        if args.v4l2_device:
            frame["conversion_ms"] = (frame["converted_ns"] - frame["ready_ns"]) / 1e6
            frame["v4l2_write_ms"] = (frame["write_done_ns"] - frame["write_start_ns"]) / 1e6
            frame["pts_to_v4l2_ms"] = (frame["write_done_ns"] + offset(frame["write_done_ns"]) - frame["pts_us"] * 1000) / 1e6
    consumer_matches = []
    if args.v4l2_device:
        with open(output / "consumer.csv") as handle:
            consumed = list(csv.DictReader(handle))
        consumer_matches = match_consumer(frames, consumed, offset)
    matched = []
    trace_status = "Assumes a REALTIME sensor clock, checked manually in this phone's camera dump; PTS-to-sensor correspondence is unverified in this run"
    if args.instrumented:
        if "PCAM_TIMESTAMP_SOURCE=1" not in (output / "server.log").read_text():
            raise RuntimeError("Instrumented server did not verify the camera REALTIME timestamp source")
        with open(output / "phone_trace.csv") as handle:
            events = list(csv.DictReader(handle))
        if any(int(event["dropped_events"]) for event in events):
            raise RuntimeError("Phone trace lost events")
        sensor = {int(event["pts_ns"]) // 1000: event for event in events if event["event"] == "sensor"}
        encoded = {int(event["pts_ns"]) // 1000: event for event in events if event["event"] == "encoded"}
        for frame in frames:
            pts = frame["pts_us"]
            if pts not in sensor or pts not in encoded:
                continue
            capture_ns = int(sensor[pts]["pts_ns"])
            encoder_ns = int(encoded[pts]["event_boot_ns"])
            matched.append(dict(pts_us=pts, sensor_ns=capture_ns,
                                sensor_pts_delta_ns=pts * 1000 - capture_ns,
                                sensor_to_encoder_ms=(encoder_ns - capture_ns) / 1e6,
                                encoder_to_receive_ms=(receipt[pts] + offset(receipt[pts]) - encoder_ns) / 1e6,
                                sensor_to_decoded_ms=(frame["ready_ns"] + offset(frame["ready_ns"]) - capture_ns) / 1e6,
                                ready_ns=frame["ready_ns"]))
        if len(matched) < .95 * len(frames):
            raise RuntimeError(f"Sensor/PTS matches {len(matched)}/{len(frames)} are below 95%")
        trace_status = "Directly verified Camera2 capture-start sensor timestamps against encoder PTS, within 1 microsecond truncation"
    for filename, rows in [("frames.csv", frames), ("packets.csv", packets),
                           ("matched_trace.csv", matched), ("consumer_matches.csv", consumer_matches)]:
        if rows:
            with open(output / filename, "w") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    (output / "clock_samples.json").write_text(json.dumps(sync, indent=2))
    stable = [frame for frame in frames if frame["ready_ns"] > frames[0]["ready_ns"] + int(args.warmup * 1e9)] if frames else []
    if len(stable) < 2:
        raise RuntimeError("Not enough decoded frames after warmup")
    if any(frame["width"] >= frame["height"] for frame in stable) and args.rotation in (90, 270):
        raise RuntimeError("Expected portrait frames, got landscape")
    if any(frame["pts_to_decoded_ms"] < 0 or frame["pts_to_decoded_ms"] > 10000 for frame in stable):
        raise RuntimeError("PTS does not match the assumed phone BOOTTIME clock")
    summary = dict(configuration=vars(args),
                   measurement="scrcpy camera presentation timestamp to CPU-accessible decoded frame; excludes V4L2 and consuming apps",
                   sensor_timestamp_status=trace_status,
                   clock_model="linear offset between minimum-RTT exchanges before and after capture; assumes bounded drift and near-symmetric paths",
                   clock_uncertainty_ms=[sample["uncertainty_ns"] / 1e6 for sample in best],
                   clock_offset_change_ms=(b["offset_ns"] - a["offset_ns"]) / 1e6,
                   decoded_frames=len(frames), analyzed_frames=len(stable),
                   received_access_units=len(packets),
                   width=stable[0]["width"], height=stable[0]["height"],
                   decoded_fps=(len(stable) - 1) * 1e9 / (stable[-1]["ready_ns"] - stable[0]["ready_ns"]),
                   pts_to_decoded_ms=percentiles([frame["pts_to_decoded_ms"] for frame in stable]),
                   receive_to_decoded_ms=percentiles([frame["receive_to_decoded_ms"] for frame in stable]),
                   pts_to_receive_ms=percentiles([(receipt[frame["pts_us"]] + offset(receipt[frame["pts_us"]]) - frame["pts_us"] * 1000) / 1e6 for frame in stable]))
    if matched:
        stable_pts = {frame["pts_us"] for frame in stable}
        selected = [frame for frame in matched if frame["pts_us"] in stable_pts]
        summary["sensor_pts_matched_frames"] = len(matched)
        sensor_times = sorted(int(event["pts_ns"]) for event in sensor.values()
                              if selected[0]["sensor_ns"] <= int(event["pts_ns"]) <= selected[-1]["sensor_ns"])
        if len(sensor_times) > 1:
            summary["sensor_capture_fps"] = (len(sensor_times) - 1) * 1e9 / (sensor_times[-1] - sensor_times[0])
        for key in ("sensor_to_encoder_ms", "encoder_to_receive_ms", "sensor_to_decoded_ms"):
            summary[key] = percentiles([frame[key] for frame in selected])
    if args.v4l2_device:
        stable_pts = {frame["pts_us"] for frame in stable}
        selected = [frame for frame in consumer_matches if frame["pts_us"] in stable_pts]
        if len(selected) < .95 * len(stable):
            raise RuntimeError(f"V4L2 consumer matched only {len(selected)}/{len(stable)} stable frames")
        summary["measurement"] = "Camera sensor presentation timestamp through YUYV conversion, V4L2 write and independent consumer dequeue; excludes application rendering"
        summary["consumer_matched_frames"] = len(consumer_matches)
        summary["consumer_matched_stable_frames"] = len(selected)
        summary["consumer_timestamp_matching"] = "Driver monotonic write timestamps within measured write calls, with 1 us quantization, plus matching sampled pixel fingerprints"
        for key in ("conversion_ms", "v4l2_write_ms", "pts_to_v4l2_ms"):
            summary[key] = percentiles([frame[key] for frame in stable])
        for key in ("pts_to_consumer_ms", "kernel_to_consumer_ms"):
            summary[key] = percentiles([frame[key] for frame in selected])
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--clock-serial", help="Optional separate USB transport for clock sync")
    parser.add_argument("--camera", default="0")
    parser.add_argument("--size", default="640x480", help="Advertised sensor size before rotation")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--rotation", type=int, choices=[0, 90, 180, 270], default=90)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--warmup", type=float, default=3)
    parser.add_argument("--bitrate", type=int, default=8_000_000)
    parser.add_argument("--high-speed", action="store_true")
    parser.add_argument("--instrumented", action="store_true", help="Verify sensor/PTS mapping and split camera/encode from transport")
    parser.add_argument("--decoder", default="h264")
    parser.add_argument("--v4l2-device", help="Optional virtual webcam output, also measured by an independent consumer")
    parser.add_argument("--v4l2-fps", type=int, default=60, help="Advertised webcam rate; does not pace frame writes")
    parser.add_argument("--codec-options", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 0 <= args.warmup < args.seconds:
        parser.error("Require 0 <= warmup < seconds")
    run(args)
