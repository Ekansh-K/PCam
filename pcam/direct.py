"""Start native Wi-Fi Direct alongside the laptop's current Wi-Fi connection."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from .run import ROOT, ADB, PACKAGE, read_utf
from .pairing import payload
from .paths import STATE, tool

IW = tool('iw', 'iw/sbin/iw')
GROUP = 'pcam-p2p0'
HOST = '172.29.67.1'
NM_CONFIG = Path('/run/NetworkManager/conf.d/90-pcam-p2p.conf')


def command(*args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, timeout=30)
    if result.returncode: raise RuntimeError(f'{Path(args[0]).name}: {result.stderr.strip()}')
    return result.stdout.strip()


def fields(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def select_phone(devices, requested=None):
    rows = [line.split() for line in devices.splitlines()[1:] if line.strip()]
    connected = [row for row in rows if len(row) >= 2 and row[1] == 'device']
    if requested:
        if any(row[0] == requested for row in connected): return requested
        raise RuntimeError('The selected phone is not connected. Connect it by USB and unlock it.')
    usb = [row for row in connected if any(value.startswith('usb:') for value in row[2:])]
    choices = usb or connected
    if len(choices) == 1: return choices[0][0]
    if len(choices) > 1: raise RuntimeError('More than one phone is connected. Select one with --serial.')
    if any(len(row) >= 2 and row[1] == 'unauthorized' for row in rows):
        raise RuntimeError('Allow USB debugging on the unlocked phone, then start PCam again.')
    raise RuntimeError('Connect the phone by USB, unlock it, and enable USB debugging. Then start PCam again.')


def prepare_webcam(device, user):
    if not Path(device).exists():
        if device != '/dev/video10': raise RuntimeError('The selected virtual webcam does not exist: ' + device)
        print('Loading the PCam virtual webcam.', flush=True)
        command('modprobe', 'v4l2loopback', 'devices=1', 'video_nr=10', 'card_label=PCam', 'exclusive_caps=1')
        command('udevadm', 'settle', '--timeout=5')
        if not Path(device).exists(): raise RuntimeError('The virtual camera driver did not create /dev/video10.')
    info = user('v4l2-ctl', '--device', device, '--info')
    if 'v4l2 loopback' not in info: raise RuntimeError('The selected device is not the PCam virtual webcam.')


def failure_reason(name, code, log):
    try: lines = [line for line in log.read_text(errors='replace').splitlines() if line.strip()]
    except OSError: lines = []
    reason = lines[-1] if lines else 'No additional details were logged.'
    return f'{name} stopped with exit code {code}. {reason} See {log}.'


def router_link(interface):
    link = command(IW, 'dev', interface, 'link')
    peer = re.search(r'Connected to (\S+)', link)
    frequency = re.search(r'freq: (\d+)', link)
    if not peer or not frequency: raise RuntimeError('Connect the laptop to its usual Wi-Fi first')
    return peer.group(1), int(frequency.group(1))


def connected_interface():
    for interface in re.findall(r'^\s*Interface (\S+)', command(IW, 'dev'), re.MULTILINE):
        try:
            router_link(interface)
            return interface
        except RuntimeError:
            continue
    raise RuntimeError('Connect the laptop to its usual Wi-Fi first')


def run(args):
    if os.geteuid() != 0:
        os.chdir(ROOT)
        os.execvp('sudo', ['sudo', sys.executable, '-m', 'pcam.direct', *sys.argv[1:]])
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0: raise ValueError('Select the desktop user with --user')
    if not args.interface: args.interface = connected_interface()
    STATE.mkdir(parents=True, exist_ok=True)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    initial = router_link(args.interface)
    phy = (Path('/sys/class/net') / args.interface / 'phy80211').resolve().name
    if (Path('/sys/class/net') / GROUP).exists() or NM_CONFIG.exists():
        raise RuntimeError('A PCam direct controller already exists; stop it before starting another')
    if any(row.get('dst') == '172.29.67.0/24' for row in json.loads(command('ip', '-j', 'route'))):
        raise RuntimeError('The temporary direct subnet is already in use')
    mac = bytearray.fromhex((Path('/sys/class/net') / args.interface / 'address').read_text().strip().replace(':', ''))
    mac[0] = (mac[0] | 2) & 254
    mac[-1] ^= 1
    group_mac = ':'.join(f'{byte:02x}' for byte in mac)
    state_dir = Path(args.state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    benchmark_output = Path(args.benchmark_output).resolve() if args.benchmark_output else STATE / 'results' / ('wifi-direct-' + str(time.time_ns()))
    if args.benchmark_seconds and benchmark_output.exists(): raise FileExistsError('Choose a new benchmark output directory')
    processes, controller, receiver = [], None, None
    created = False
    state = {}
    session_path = state_dir / 'session.json'
    qr_path = state_dir / 'qr.json'

    def user(*values): return command('runuser', '-u', args.user, '--', *values)
    def spawn_user(values, log):
        return subprocess.Popen([sys.executable, *values], user=account.pw_uid, group=account.pw_gid,
            extra_groups=os.getgrouplist(args.user, account.pw_gid), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT)
    def adb(*values): return user(ADB, '-s', args.serial, *values)
    def wpa(*values): return command('wpa_cli', '-p', runtime, '-i', GROUP, *values)
    def check_router():
        if router_link(args.interface) != initial:
            raise RuntimeError('Router connection or channel changed; stopping the direct link')
    def stop_signal(*unused): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    if args.desktop:
        from .session import desktop_stop
        desktop_stop()
    if args.qr and args.benchmark_seconds: raise RuntimeError('Timestamp tracing still requires the USB benchmark route')
    if not args.qr: args.serial = select_phone(user(ADB, 'devices', '-l'), args.serial)
    prepare_webcam(args.device, user)
    # Fail before changing Wi-Fi if the phone is missing or the webcam cannot open.
    if not args.qr: adb('get-state')
    user(sys.executable, '-c', 'import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY); os.close(fd)', args.device)
    network_logs = [(state_dir / 'wifi.log', 'Wi-Fi Direct controller'), (state_dir / 'dhcp.log', 'Direct address service')]
    def check_processes():
        for index, process in enumerate(processes):
            code = process.poll()
            if code is not None:
                log, name = network_logs[index] if index < 2 else (STATE / 'live-color.log', 'Webcam receiver')
                raise RuntimeError(failure_reason(name, code, log))
    with tempfile.TemporaryDirectory(prefix='pcam-p2p-', dir='/run') as runtime:
        try:
            NM_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            NM_CONFIG.write_text('[device-pcam-direct]\nmatch-device=interface-name:' + GROUP + '\nmanaged=0\n')
            command('nmcli', 'general', 'reload', 'conf')
            # The normal station slot is occupied. Initialize as AP so this
            # supplicant can switch directly to P2P-GO without a second station.
            command(IW, 'phy', phy, 'interface', 'add', GROUP, 'type', '__ap')
            created = True
            command('ip', 'link', 'set', GROUP, 'address', group_mac)
            config = Path(runtime) / 'wpa.conf'
            config.write_text('ctrl_interface=' + runtime + '\ndevice_name=PCam-laptop\n'
                              'device_type=1-0050F204-1\nconfig_methods=virtual_push_button\n'
                              'p2p_no_group_iface=1\np2p_go_intent=15\np2p_ssid_postfix=-PCam\n')
            config.chmod(0o600)
            with (state_dir / 'wifi.log').open('w') as log:
                controller = subprocess.Popen(['wpa_supplicant', '-Dnl80211', '-i', GROUP, '-c', str(config)],
                                              stdout=log, stderr=subprocess.STDOUT)
            processes.append(controller)
            for _ in range(40):
                if (Path(runtime) / GROUP).exists(): break
                if controller.poll() is not None: raise RuntimeError('Direct controller failed; inspect wifi.log')
                time.sleep(.1)
            if wpa('p2p_group_add', 'freq=' + str(initial[1])) != 'OK':
                raise RuntimeError('Wi-Fi Direct group creation failed')
            for _ in range(40):
                status = fields(wpa('status'))
                if status.get('wpa_state') == 'COMPLETED': break
                time.sleep(.1)
            if status.get('mode') != 'P2P GO' or int(status.get('freq', 0)) != initial[1]:
                raise RuntimeError('The group did not start on the router channel')
            check_router()
            password = wpa('p2p_get_passphrase')
            command('ip', 'addr', 'add', HOST + '/24', 'dev', GROUP)
            with (state_dir / 'dhcp.log').open('w') as log:
                processes.append(subprocess.Popen(['dnsmasq', '--keep-in-foreground', '--port=0', '--bind-interfaces',
                    '--interface=' + GROUP, '--dhcp-range=172.29.67.2,172.29.67.20,255.255.255.0,1h',
                    '--dhcp-option=3', '--dhcp-option=6', '--conf-file=/dev/null',
                    '--dhcp-leasefile=' + str(Path(runtime) / 'leases')], stdout=log, stderr=subprocess.STDOUT))
            token = secrets.token_hex(16)
            if args.qr:
                qr = payload(status['ssid'], password, status['p2p_device_address'], initial[1], HOST, token)
                qr_path.touch(mode=0o600, exist_ok=True); qr_path.chmod(0o600)
                qr_path.write_text(json.dumps(qr)); os.chown(qr_path, account.pw_uid, account.pw_gid)
                print('Waiting for the phone to scan the pairing QR.', flush=True)
            else:
                adb('shell', 'am', 'force-stop', PACKAGE)
                adb('shell', 'am', 'start', '-n', PACKAGE + '/.MainActivity', '--es', 'host', HOST, '--es', 'token', token,
                    '--es', 'direct_ssid', status['ssid'], '--es', 'direct_passphrase', password,
                    '--es', 'direct_device', status['p2p_device_address'], '--ei', 'direct_frequency', str(initial[1]),
                    '--ez', 'direct_connect', 'true')
                print('Connecting the phone over Wi-Fi Direct. The router connection stays active.', flush=True)
            deadline = time.monotonic() + (300 if args.qr else 45)
            phone = None; peer_details = {}
            while time.monotonic() < deadline:
                check_router()
                check_processes()
                if args.qr:
                    leases = Path(runtime) / 'leases'
                    rows = leases.read_text().splitlines() if leases.exists() else []
                    candidates = [row.split()[2] for row in rows if len(row.split()) >= 3 and re.fullmatch(r'172\.29\.67\.\d+', row.split()[2])]
                else:
                    addresses = adb('shell', 'ip', '-4', 'addr', 'show', 'p2p0')
                    candidates = re.findall(r'inet (172\.29\.67\.\d+)/', addresses)
                for candidate in candidates:
                    try:
                        with socket.create_connection((candidate, 27185), timeout=.5) as control:
                            control.settimeout(1)
                            control.sendall(struct.pack('>H', len(token)) + token.encode())
                            if read_utf(control) != 'PCAM/1': raise RuntimeError('Unexpected phone protocol')
                            peer_details = json.loads(read_utf(control))
                            if peer_details.get('wifi_direct', {}).get('owner_address') == HOST:
                                phone = candidate; break
                    except (OSError, EOFError): pass
                if phone: break
                time.sleep(.25)
            else: raise RuntimeError('Pairing timed out. Show a new QR and scan it from PCam on the phone.' if args.qr else 'Phone did not join. Check its nearby-device permission and direct status.')
            qr_path.unlink(missing_ok=True)
            state = dict(phone=phone, host=HOST, token=token, interface=GROUP, frequency=initial[1], name=peer_details.get('name', peer_details.get('model','Phone')))
            session_path.touch(mode=0o600, exist_ok=True)
            session_path.chmod(0o600)
            session_path.write_text(json.dumps(state))
            os.chown(session_path, account.pw_uid, account.pw_gid)
            if args.benchmark_seconds:
                with socket.create_connection((state['phone'], 27185), timeout=5) as control:
                    control.settimeout(5)
                    control.sendall(struct.pack('>H', len(token)) + token.encode())
                    if read_utf(control) != 'PCAM/1': raise RuntimeError('Unexpected phone protocol')
                    details = json.loads(read_utf(control))
                print('Measuring camera timestamps over Wi-Fi Direct.', flush=True)
                with (state_dir / 'benchmark.log').open('w') as log:
                    measurement = spawn_user(['-m', 'pcam.run',
                        '--phone', state['phone'], '--bind', HOST, '--serial', args.serial, '--session-file', str(session_path),
                        '--output-size', str(details['output_width']), str(details['output_height']),
                        '--vendor-low-latency', '--device', args.device, '--seconds', str(args.benchmark_seconds), '--output', str(benchmark_output)], log)
                processes.append(measurement)
                while measurement.poll() is None:
                    check_router()
                    time.sleep(.5)
                processes.remove(measurement)
                if measurement.returncode: raise RuntimeError('Timestamp benchmark failed; inspect benchmark.log')
                summary = json.loads((benchmark_output / 'summary.json').read_text())
                latency = summary['pts_to_consumer_ms']
                print(f"Latency median {latency['p50']:.1f} ms, p95 {latency['p95']:.1f} ms. Results: {benchmark_output}", flush=True)
            print('Direct connection ready. Starting the portrait webcam.', flush=True)
            with (STATE / 'live-color.log').open('w') as log:
                os.fchown(log.fileno(), account.pw_uid, account.pw_gid)
                values = ['-m', 'pcam.live', '--phone', state['phone'], '--session-file', str(session_path), '--device', args.device]
                if args.settings_file: values += ['--settings-file', args.settings_file]
                receiver = spawn_user(values, log)
            processes.append(receiver)
            (STATE / 'live-color.pid').write_text(str(receiver.pid) + '\n')
            os.chown(STATE / 'live-color.pid', account.pw_uid, account.pw_gid)
            (state_dir / 'controller.pid').write_text(str(os.getpid()) + '\n')
            while True:
                check_router()
                if receiver.poll() == 0:
                    print('Phone disconnected.', flush=True)
                    break
                check_processes()
                time.sleep(.5)
        except KeyboardInterrupt:
            print('Stopping Wi-Fi Direct. The router connection stays active.', flush=True)
        finally:
            # Stop video before removing its route, and ask Android to remove
            # this group without changing its ordinary Wi-Fi connection.
            if receiver and receiver.poll() is None:
                receiver.send_signal(signal.SIGINT)
                try: receiver.wait(timeout=5)
                except subprocess.TimeoutExpired: receiver.kill(); receiver.wait()
            if state:
                try:
                    with socket.create_connection((state['phone'], 27185), timeout=2) as control:
                        control.settimeout(2)
                        control.sendall(struct.pack('>H', len(state['token'])) + state['token'].encode())
                        read_utf(control); read_utf(control)
                        control.sendall(b'\x07'); control.recv(1)
                except (OSError, EOFError): pass
            for process in reversed(processes):
                if process.poll() is not None: continue
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            if created: subprocess.run([str(IW), 'dev', GROUP, 'del'], capture_output=True)
            NM_CONFIG.unlink(missing_ok=True)
            subprocess.run(['nmcli', 'general', 'reload', 'conf'], capture_output=True)
            session_path.unlink(missing_ok=True)
            qr_path.unlink(missing_ok=True)
            (state_dir / 'controller.pid').unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', help='Wi-Fi adapter; automatically selects the connected adapter by default')
    parser.add_argument('--serial', help='Phone to pair; automatically selects the connected USB phone by default')
    parser.add_argument('--qr', action='store_true', help='Pair by scanning the desktop QR, without ADB or USB')
    parser.add_argument('--user', default=os.environ.get('SUDO_USER') or pwd.getpwuid(os.getuid()).pw_name)
    parser.add_argument('--device', default='/dev/video10')
    parser.add_argument('--settings-file', help='Private output settings supplied by the desktop app')
    parser.add_argument('--desktop', action='store_true', help='Accept stop requests from the desktop app')
    parser.add_argument('--state-dir', default=str(STATE / 'direct'))
    parser.add_argument('--benchmark-seconds', type=float, default=0, help='Measure timestamps before starting the live webcam')
    parser.add_argument('--benchmark-output', help='New output directory for an optional timestamp benchmark')
    args = parser.parse_args()
    if args.benchmark_seconds and args.benchmark_seconds <= 3: parser.error('Use more than three seconds for camera warmup')
    try: run(args)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('PCam: ' + str(error), file=sys.stderr)
        sys.exit(1)
