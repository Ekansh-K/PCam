"""Measure sensor-to-CPU-buffer latency before implementing a replacement ISP path."""
import argparse
import csv
import json
import secrets
import socket
import struct
import time
from pathlib import Path
from pcam.run import adb, read_utf, PACKAGE
from pcam.metrics import exact, percentiles

FORMATS = {'raw16': 32, 'raw10': 37, 'yuv': 35}


def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    token = secrets.token_hex(16)
    width = args.width or (320 if args.format == 'yuv' else 4096)
    height = args.height or (240 if args.format == 'yuv' else 3072)
    adb(args.serial, 'shell', 'am', 'force-stop', PACKAGE)
    adb(args.serial, 'shell', 'am', 'start', '-n', PACKAGE + '/.MainActivity',
        '--es', 'token', token, '--es', 'host', '127.0.0.1', '--es', 'camera', args.camera,
        '--ei', 'width', str(width), '--ei', 'height', str(height),
        '--ei', 'fps', str(args.fps), '--ei', 'min_fps', str(args.fps), '--ez', 'high_speed', 'false',
        '--ez', 'trial_unadvertised_fps', str(args.trial_unadvertised_fps).lower(),
        '--ei', 'trial_pcr', str(args.trial_pcr),
        '--ei', 'buffer_probe', str(FORMATS[args.format]), '--ez', 'trace_enabled', 'true',
        '--el', 'exposure_ns', str(args.exposure_ns))
    port = int(adb(args.serial, 'forward', 'tcp:0', 'tcp:27185').strip())
    control = None
    try:
        for _ in range(50):
            try:
                control = socket.create_connection(('127.0.0.1', port), timeout=2)
                control.sendall(struct.pack('>H', len(token)) + token.encode())
                if read_utf(control) != 'PCAM/1': raise ValueError('Unexpected protocol')
                status = json.loads(read_utf(control))
                break
            except (OSError, EOFError):
                if control: control.close()
                control = None
                time.sleep(.1)
        else:
            raise RuntimeError('Probe control unavailable')
        if status['timestamp_source'] != 1: raise ValueError('Sensor clock must be REALTIME')
        print(f'Measuring {args.format} {width}x{height}, sensor to mapped CPU buffer; no images saved.', flush=True)
        control.sendall(b'\x05')
        time.sleep(args.seconds)
        control.sendall(b'\x04')
        status = json.loads(read_utf(control))
    finally:
        try:
            if control:
                try:
                    control.sendall(b'\x03'); exact(control, 1)
                finally:
                    control.close()
        finally:
            adb(args.serial, 'forward', '--remove', f'tcp:{port}')
    for _ in range(50):
        raw = adb(args.serial, 'exec-out', 'run-as', PACKAGE, 'cat', 'files/trace.csv')
        if 'trace_dropped,' in raw: break
        time.sleep(.1)
    (output / 'phone_trace.csv').write_text(raw)
    (output / 'phone_status.json').write_text(json.dumps(status, indent=2) + '\n')
    pid = adb(args.serial, 'shell', 'pidof', PACKAGE).strip()
    (output / 'phone.log').write_text(adb(args.serial, 'logcat', '-d', '--pid=' + pid, '-t', '1500'))
    trace = list(csv.DictReader(raw.splitlines()))
    if not trace or trace[-1]['event'] != 'trace_dropped' or int(trace[-1]['value']):
        raise RuntimeError('Incomplete probe trace')
    sensors = {int(r['pts_ns']): r for r in trace if r['event'] == 'sensor'}
    readouts = {r['frame_id']: int(r['pts_ns']) for r in trace if r['event'] == 'readout'}
    buffers = [r for r in trace if r['event'] == 'camera_buffer']
    if not buffers: raise RuntimeError('No camera buffers; inspect phone.log')
    stable = [r for r in buffers if int(r['time_ns']) > int(buffers[0]['time_ns']) + args.warmup * 1e9]
    matched = [r for r in stable if int(r['pts_ns']) in sensors]
    if len(matched) < 30 or len(matched) < .95 * len(stable):
        raise RuntimeError('Insufficient exact sensor matches')
    result = dict(configuration=vars(args), width=status['actual_buffer_width'], height=status['actual_buffer_height'],
                  actual_format=status['actual_buffer_format'],
                  measurement='Sensor exposure timestamp to mapped CPU-readable image; phone clock only; excludes encoding, software ISP, network and V4L2',
                  exact_sensor_matches=len(matched), matched_fraction=len(matched)/len(stable),
                  sensor_to_buffer_ms=percentiles([(int(r['time_ns'])-int(r['pts_ns']))/1e6 for r in matched]),
                  pipeline_depth=percentiles([int(r['value']) for r in trace if r['event']=='pipeline_depth']),
                  exposure_ms=percentiles([int(r['value'])/1e6 for r in trace if r['event']=='exposure']),
                  rolling_skew_ms=percentiles([int(r['value'])/1e6 for r in trace if r['event']=='rolling_skew']))
    result['frame_duration_ms'] = percentiles([int(r['value'])/1e6 for r in trace if r['event']=='frame_duration'])
    result['sensor_mode_result'] = sorted({int(r['value']) for r in trace if r['event']=='sensor_mode'})
    result['readout_to_buffer_ms'] = percentiles([(int(r['time_ns'])-readouts[sensors[int(r['pts_ns'])]['frame_id']])/1e6 for r in matched
                                                if sensors[int(r['pts_ns'])]['frame_id'] in readouts])
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='b6d3efda')
    parser.add_argument('--camera', default='0')
    parser.add_argument('--format', choices=FORMATS, default='raw16')
    parser.add_argument('--width', type=int); parser.add_argument('--height', type=int)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--trial-unadvertised-fps', action='store_true')
    parser.add_argument('--trial-pcr', type=int, choices=[-1, 0, 1, 2], default=-1)
    parser.add_argument('--seconds', type=float, default=15)
    parser.add_argument('--warmup', type=float, default=3)
    parser.add_argument('--exposure-ns', type=int, default=1_000_000)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 0 <= args.warmup < args.seconds: parser.error('Require 0 <= warmup < seconds')
    run(args)
