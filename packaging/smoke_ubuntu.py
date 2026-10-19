#!/usr/bin/env python3
"""Exercise the installed Ubuntu app as an ordinary user in a clean container."""
import ctypes as C
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, '/usr/lib/pcam')
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication
from pcam import native
from pcam.desktop import Window
from pcam.pairing import payload, qr_pixels
from pcam.paths import ROOT, STATE, PACKAGED

assert os.geteuid() != 0, 'Run this check as a non-root user'
assert PACKAGED and ROOT == Path('/usr/lib/pcam')
assert STATE == Path.home() / '.cache/pcam'
assert not os.access(ROOT, os.W_OK), 'Installed code must be read-only for the desktop user'
STATE.mkdir(parents=True, exist_ok=True)
with patch('pcam.native.subprocess.run', side_effect=AssertionError('Packaged app must not compile at runtime')):
    library = native.build()
    decoder = library.decoder_open(b'h264')
    assert decoder
    image = Path(sys.argv[1]).read_bytes()
    frames = (native.Frame * 16)()
    count = library.decoder_feed(decoder, image, len(image), 123456, frames, len(frames))
    assert count == 1 and (frames[0].width, frames[0].height) == (64, 32)
    library.decoder_close(decoder)
details = payload('DIRECT-00-PCam', 'test-password', '02:00:00:00:00:01', 5745, '172.29.67.1', '0' * 32)
qr = qr_pixels(details)
assert qr.ndim == 2 and qr.shape[0] == qr.shape[1] and qr.min() == 0 and qr.max() == 255
app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
    window = Window()
    window.poll.stop()
    with patch.object(window, 'scan'):
        window.set_mode('direct')
        assert window.left_stack.currentWidget() is window.qr_panel
        window.set_mode('shared')
        assert window.left_stack.currentWidget() is window.devices_panel
    window.resolution.buttons[1].click()
    window.resolution.landscape_button.click()
    assert window.resolution.output_size() == (1600, 720)
    window.close()
print(json.dumps(dict(installed_app=True, non_root=True, no_runtime_compiler=True,
    h264_decoded=[64, 32], qr_generated=True, desktop_controls=True)))
