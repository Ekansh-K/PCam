"""Benchmark a temporary laptop-hosted 5 GHz link, then restore the saved networks."""
import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT / 'scratch/tools/platform-tools/adb'


def command(*args, check=True, timeout=40):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def run(args):
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError('Choose a new benchmark output directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    original = command('nmcli', '-g', 'GENERAL.CONNECTION', 'device', 'show', args.interface)
    if not original or original == '--':
        raise RuntimeError('Connect the laptop to its usual saved network before the test')

    def adb(*values):
        return command(ADB, '-s', args.serial, *values)

    phone_status = adb('shell', 'cmd', 'wifi', 'status')
    original_ssid = re.search(r'Wifi is connected to "([^"]+)"', phone_status)
    profile = 'PCam-test-' + secrets.token_hex(3)
    password = secrets.token_urlsafe(18)
    result = {'topology': 'Laptop-hosted 5 GHz AP, no router or phone hotspot',
              'channel': args.channel, 'phone_setup': 'USB ADB', 'video_and_clock': 'Direct Wi-Fi'}
    try:
        command('nmcli', 'con', 'add', 'type', 'wifi', 'ifname', args.interface,
                'con-name', profile, 'ssid', profile, '802-11-wireless.mode', 'ap',
                '802-11-wireless.band', 'a', '802-11-wireless.channel', args.channel,
                'ipv4.method', 'shared', 'ipv4.addresses', '172.29.66.1/24',
                'ipv6.method', 'disabled', 'wifi-sec.key-mgmt', 'wpa-psk',
                'wifi-sec.psk', password, 'connection.autoconnect', 'no')
        print('Starting temporary 5 GHz laptop network. The usual Wi-Fi will reconnect after the test.', flush=True)
        command('nmcli', '-w', '20', 'con', 'up', profile)
        adb('shell', 'cmd', 'wifi', 'connect-network', profile, 'wpa2', password, '-d')
        for _ in range(120):
            match = re.search(r'inet (172\.29\.66\.\d+)/', adb('shell', 'ip', '-4', 'addr', 'show', 'wlan0'))
            if match:
                phone = match.group(1)
                break
            time.sleep(.25)
        else:
            raise RuntimeError('Phone did not obtain a direct-link address')
        result['wifi_status'] = adb('shell', 'cmd', 'wifi', 'status')
        print(f'Phone joined the direct link at {phone}. Running the timestamp benchmark.', flush=True)
        stream_options = ['--fps', '120', '--min-fps', '120', '--width', '1280', '--height', '720', '--bitrate', '12000000']
        if args.trial_raw_stream:
            stream_options = ['--fps', '60', '--no-high-speed', '--trial-unadvertised-fps',
                              '--trial-raw-stream', '--trial-raw-native', '--raw-content-height', '2304',
                              '--width', str(args.width), '--height', str(args.height),
                              '--exposure-ns', str(args.exposure_ns), '--sensitivity', str(args.sensitivity),
                              '--bitrate', str(args.bitrate), '--trial-encoder-fps', str(args.encoder_fps)]
            if args.snapshot: stream_options.append('--snapshot')
            if args.trial_raw_uncompressed: stream_options.append('--trial-raw-uncompressed')
        with output.with_suffix('.log').open('w') as log:
            subprocess.run([sys.executable, '-m', 'pcam.run', '--phone', phone, '--serial', args.serial,
                            '--bind', '172.29.66.1', *stream_options,
                            '--vendor-low-latency', '--seconds', str(args.seconds), '--output', str(output)],
                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True,
                           timeout=args.seconds + 90)
        summary = json.loads((output / 'summary.json').read_text())
        rate = 'output_fps' if 'output_fps' in summary else 'decoded_fps'
        print(json.dumps({key: summary[key] for key in
                          ('pts_to_consumer_ms', 'clock_uncertainty_ms', rate,
                           'steady_state_frame_delivery_fraction')}, indent=2), flush=True)
    finally:
        command('nmcli', 'con', 'down', profile, check=False)
        try:
            for line in adb('shell', 'cmd', 'wifi', 'list-networks').splitlines():
                if profile in line:
                    adb('shell', 'cmd', 'wifi', 'forget-network', line.split()[0])
        finally:
            command('nmcli', 'con', 'delete', profile, check=False)
            command('nmcli', '-w', '30', 'con', 'up', 'id', original, 'ifname', args.interface)
        if original_ssid:
            for _ in range(60):
                if f'Wifi is connected to "{original_ssid.group(1)}"' in adb('shell', 'cmd', 'wifi', 'status'):
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('Laptop network restored; phone has not rejoined its previous network')
        print('Temporary network removed. Both devices restored to their previous networks.', flush=True)
        if output.exists():
            (output / 'link.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', default='wlo1')
    parser.add_argument('--serial', default='b6d3efda')
    parser.add_argument('--channel', type=int, default=149)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--trial-raw-stream', action='store_true', help='Experimental monochrome RAW path at portrait 180x320 and 60 FPS')
    parser.add_argument('--trial-raw-uncompressed', action='store_true', help='Send the downsampled RAW preview without video compression')
    parser.add_argument('--exposure-ns', type=int, default=1_000_000, help='Manual exposure for the RAW experiment')
    parser.add_argument('--sensitivity', type=int, default=400, help='ISO for the RAW experiment')
    parser.add_argument('--encoder-fps', type=int, default=240, help='Encoder scheduling rate for the RAW experiment')
    parser.add_argument('--bitrate', type=int, default=4_000_000, help='Bitrate for the RAW experiment')
    parser.add_argument('--snapshot', action='store_true', help='Save the last received RAW preview after timing ends')
    parser.add_argument('--width', type=int, default=320, help='Downsampled RAW output width')
    parser.add_argument('--height', type=int, default=180, help='Downsampled RAW output height')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.trial_raw_uncompressed: args.trial_raw_stream = True
    if args.seconds <= 3:
        parser.error('Use more than three seconds to allow camera warmup')
    run(args)
