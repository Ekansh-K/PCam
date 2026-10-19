"""Small privileged backend for the desktop app's shared-Wi-Fi session."""
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import threading
import time
from .direct import prepare_webcam, failure_reason
from .paths import STATE


def desktop_stop():
    """The owner can stop the root helper through its inherited private pipe."""
    def watch():
        for line in sys.stdin:
            if line.strip() == 'stop': break
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch, daemon=True).start()


def run(args):
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0 or os.geteuid() not in (0, account.pw_uid):
        raise RuntimeError('Start the camera as the desktop user')
    def user(*values):
        command = (['runuser', '-u', args.user, '--'] if os.geteuid() == 0 else []) + list(map(str, values))
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode: raise RuntimeError(result.stderr.strip())
        return result.stdout
    prepare_webcam(args.device, user)
    session = Path(args.session_file).resolve()
    if session.stat().st_uid != account.pw_uid or session.stat().st_mode & 0o077:
        raise RuntimeError('Pairing file must be private and owned by the desktop user')
    details = json.loads(session.read_text())
    process = None
    def stop(*unused): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    desktop_stop()
    try:
        print('Starting your shared Wi-Fi camera.', flush=True)
        STATE.mkdir(parents=True, exist_ok=True)
        with (STATE / 'live-color.log').open('w') as log:
            if os.geteuid() == 0: os.fchown(log.fileno(), account.pw_uid, account.pw_gid)
            command = [sys.executable, '-u', '-m', 'pcam.live', '--phone', details['phone'],
                       '--session-file', str(session), '--control-port', str(details.get('control_port', 27185)), '--device', args.device]
            if args.settings_file: command += ['--settings-file', args.settings_file]
            identity = dict(user=account.pw_uid, group=account.pw_gid,
                extra_groups=os.getgrouplist(args.user, account.pw_gid)) if os.geteuid() == 0 else {}
            process = subprocess.Popen(command, **identity, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        (STATE / 'live-color.pid').write_text(str(process.pid) + '\n')
        if os.geteuid() == 0: os.chown(STATE / 'live-color.pid', account.pw_uid, account.pw_gid)
        while process.poll() is None: time.sleep(.2)
        if process.returncode: raise RuntimeError(failure_reason('Camera', process.returncode, STATE / 'live-color.log'))
    except KeyboardInterrupt: print('Camera disconnected.', flush=True)
    finally:
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--session-file', required=True)
    parser.add_argument('--device', default='/dev/video10')
    parser.add_argument('--settings-file')
    try: run(parser.parse_args())
    except (RuntimeError, OSError, ValueError) as error:
        print('PCam: ' + str(error), file=sys.stderr); sys.exit(1)
