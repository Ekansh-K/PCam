"""Summarize phone camera stages using directly joined sensor and readout timestamps."""
import argparse
import csv
import json
from pathlib import Path
from pcam.metrics import percentiles


def summarize(directory, warmup=3):
    with (directory / 'phone_trace.csv').open() as handle:
        rows = list(csv.DictReader(handle))
    if not rows or rows[-1]['event'] != 'trace_dropped' or int(rows[-1]['value']):
        raise ValueError('Need a complete trace without metadata loss')
    first = min(int(r['time_ns']) for r in rows)
    rows = [r for r in rows if int(r['time_ns']) >= first + warmup * 1e9]
    sensors = {r['frame_id']: r for r in rows if r['event'] == 'sensor'}
    readouts = {r['frame_id']: r for r in rows if r['event'] == 'readout'}
    by_pts = {int(r['pts_ns']) // 1000: r for r in sensors.values()}
    result = {'measurement': 'Phone-clock timestamps only; sensor-matched samples; excludes network and V4L2',
              'sample_scope': 'All matched callbacks, including encoded frames later dropped; use the webcam summary for delivered-frame latency',
              'pipeline_depth': percentiles([int(r['value']) for r in rows if r['event'] == 'pipeline_depth']),
              'rolling_skew_ms': percentiles([int(r['value']) / 1e6 for r in rows if r['event'] == 'rolling_skew'])}
    for event in ['sensor', 'exposure', 'camera_buffer', 'encoded']:
        samples = [r for r in rows if r['event'] == event and int(r['pts_ns']) // 1000 in by_pts]
        # The encoded callback records its actual arrival before copying the bytes.
        label = {'sensor': 'start_callback', 'exposure': 'capture_result'}.get(event, event)
        result['sensor_to_' + label + '_ms'] = percentiles([
            ((int(r['value']) if event == 'encoded' else int(r['time_ns'])) -
             int(by_pts[int(r['pts_ns']) // 1000]['pts_ns'])) / 1e6 for r in samples])
    result['exposure_start_to_readout_start_ms'] = percentiles([
        (int(r['pts_ns']) - int(sensors[key]['pts_ns'])) / 1e6
        for key, r in readouts.items() if key in sensors])
    result['readout_note'] = 'Rolling skew is reported sensor readout duration, not total camera or ISP latency. Pipeline-depth metadata does not identify which stage is removable.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--warmup', type=float, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(summarize(args.directory, args.warmup), indent=2) + '\n'
    if args.output: args.output.write_text(result)
    print(result)
