"""One-session Wi-Fi Direct pairing, transferred optically to the phone."""
import json
import socket
import time


def computer_name():
    try:
        from pathlib import Path
        for line in Path('/etc/machine-info').read_text().splitlines():
            if line.startswith('PRETTY_HOSTNAME='): return line.split('=', 1)[1].strip('"')[:80]
    except OSError: pass
    return socket.gethostname().split('.')[0][:80]


def payload(ssid, password, device, frequency, host, token):
    return dict(protocol='PCAM-DIRECT/1', ssid=ssid, passphrase=password, device=device,
                frequency=frequency, host=host, token=token, computer=computer_name(), expires=int(time.time()) + 300)


def qr_pixels(details):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scratch/tools/python'))
    import qrcode
    import numpy as np
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(json.dumps(details, separators=(',', ':'), ensure_ascii=True)); code.make(fit=True)
    return np.where(np.array(code.get_matrix()), 0, 255).astype(np.uint8)
