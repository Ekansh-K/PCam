"""Receive the custom phone webcam continuously. No ADB connection is used."""
import argparse
import ctypes as C
import json
import socket
import struct
import time
import os
from pathlib import Path
from . import native
from .rtp import Receiver
from .run import read_utf
from .metrics import exact
from .settings import read_size, read_settings
from .discovery import pair
from .pairing import computer_name

def output_size(details, override=None):
    return tuple(override or (details.get('output_width', 480), details.get('output_height', 960)))


def desktop_settings(control, details, path):
    if not path: return details
    size = read_size(path)
    if output_size(details) != size:
        control.sendall(b'\x08' + struct.pack('>II', *size))
        return json.loads(read_utf(control))
    return details

def run(args):
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); route.connect((args.phone, args.control_port))
    host = route.getsockname()[0]; route.close()
    print(f"Connecting to the phone from {host}.", flush=True)
    session = json.loads(Path(args.session_file).read_text()) if args.session_file else None
    if session and session['phone'] != args.phone: raise ValueError("Session phone does not match --phone")
    if session: token = session['token']
    elif args.token: token = args.token
    else:
        print('Approve the connection in PCam on your phone.', flush=True)
        token = pair(args.phone, args.control_port)['token']
    library = native.build(); handle = library.decoder_open(b"h264")
    if not handle: raise RuntimeError("Cannot open decoder")
    control, video = None, None
    started = False
    try:
        control = socket.create_connection((args.phone, args.control_port), timeout=5)
        control.settimeout(5); control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        control.sendall(struct.pack(">H", len(token)) + token.encode())
        if read_utf(control) != "PCAM/1": raise RuntimeError("Unexpected phone protocol")
        details = json.loads(read_utf(control))
        identity = json.dumps(dict(name=computer_name(), connection='Wi-Fi Direct' if host.startswith('172.29.67.') else 'Shared Wi-Fi')).encode()
        control.sendall(b'\x09' + struct.pack('>H', len(identity)) + identity); exact(control, 1)
        details = desktop_settings(control, details, args.settings_file)
        size = output_size(details, args.output_size)
        rotation = args.rotation if args.rotation is not None else 90 if size[1] >= size[0] else 0
        if args.settings_file: _, rotation = read_settings(args.settings_file)
        if library.decoder_set_rotation(handle, rotation) < 0: raise RuntimeError('Unsupported camera orientation')
        if library.decoder_set_size(handle, *size) < 0:
            raise ValueError("Output dimensions must be even and between 2 and 4096")
        control.sendall(b"\x06\x00"); exact(control, 1)
        status = library.decoder_set_output(handle, args.device.encode(), details["requested_fps"])
        if status < 0:
            raise RuntimeError(f"Cannot open {args.device}: {os.strerror(-status)}. Start PCam with ./start-pcam to prepare the webcam automatically.")
        video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        video.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        video.bind((host, args.port)); video.settimeout(.5)
        gray = details.get('video_payload') == 'GRAY8'
        receiver = Receiver(args.deadline_ms, gray=gray); decoded = (native.Frame * 16)()
        control.sendall(b"\x05")
        started = True
        print(f"PCam feeds {args.device} at {size[0]}×{size[1]}. Press Ctrl+C to stop.", flush=True)
        last_report, count, last_idr = time.monotonic(), 0, 0
        last_settings = last_report
        while True:
            if time.monotonic() - last_settings >= 1:
                control.sendall(b"\x04"); details = json.loads(read_utf(control))
                details = desktop_settings(control, details, args.settings_file)
                selected = output_size(details, args.output_size)
                if args.settings_file:
                    _, rotation = read_settings(args.settings_file)
                else: rotation = args.rotation if args.rotation is not None else 90 if selected[1] >= selected[0] else 0
                if library.decoder_set_rotation(handle, rotation) < 0: raise RuntimeError('Unsupported camera orientation')
                if selected != size:
                    if library.decoder_set_size(handle, *selected) < 0: raise ValueError("Invalid phone output size")
                    size = selected
                    print(f"Selected {size[0]}×{size[1]}. Reopen your video app's camera to apply the size.", flush=True)
                last_settings = time.monotonic()
            try: data, source = video.recvfrom(2048)
            except socket.timeout: receiver.expire(time.monotonic_ns()); continue
            now = time.monotonic_ns()
            if source[0] != args.phone: continue
            unit = receiver.push(data, now)
            if receiver.request_idr and now - last_idr > 100_000_000:
                control.sendall(b"\x02"); receiver.request_idr = False; last_idr = now
            if unit is not None:
                if gray:
                    w, h = struct.unpack_from('>HH', unit.data, 4)
                    result = library.decoder_feed_gray(handle, unit.data[8:], len(unit.data) - 8, unit.pts_ns // 1000, w, h, decoded)
                else:
                    result = library.decoder_feed(handle, unit.data, len(unit.data), unit.pts_ns // 1000, decoded, len(decoded))
                if result < 0: raise RuntimeError(f"Video output failed: {result}")
                count += result
            elapsed = time.monotonic() - last_report
            if elapsed >= 2:
                print(f"{count / elapsed:.0f} FPS, {receiver.lost_frames} recovery events", flush=True)
                count = 0; last_report = time.monotonic()
    except KeyboardInterrupt:
        print("Camera stopped.", flush=True)
    except (EOFError, ConnectionError):
        if not started: raise RuntimeError('The phone disconnected before the camera could start') from None
        print("Phone disconnected.", flush=True)
    finally:
        if control:
            try: control.sendall(b"\x03")
            except OSError: pass
            control.close()
        if video: video.close()
        library.decoder_close(handle)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--token", help="Omit to approve the connection on the phone")
    parser.add_argument("--session-file", help="Read private pairing details created by the direct connection helper")
    parser.add_argument("--device", default="/dev/video10")
    parser.add_argument("--settings-file", help="Private output settings supplied by the desktop app")
    parser.add_argument("--rotation", type=int, choices=[0, 90, 270], help="Override rotation; otherwise follows the selected orientation")
    parser.add_argument("--output-size", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"),
                        help="Override the phone's saved webcam size; default is 480×960 portrait")
    parser.add_argument("--port", type=int, default=5004); parser.add_argument("--control-port", type=int, default=27185)
    parser.add_argument("--deadline-ms", type=float, default=12)
    run(parser.parse_args())
