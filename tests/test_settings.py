"""PC-owned resolution settings and their authenticated phone command."""
import json
import socket
import struct
import tempfile
import threading
from pathlib import Path
import unittest
from pcam.live import desktop_settings
from pcam.settings import read_size, write_size
from pcam.metrics import exact


class SettingsTests(unittest.TestCase):
    def test_only_supported_presets_are_written_privately(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            write_size(path, (480, 960))
            self.assertEqual(read_size(path), (480, 960))
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            write_size(path, (720, 1600))
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                write_size(path, (1280, 720))
            self.assertEqual(read_size(path), (720, 1600))
            write_size(path, (1600, 720))
            self.assertEqual(read_size(path), (1600, 720))

    def test_live_update_sends_preset_and_uses_phone_acknowledgement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'; write_size(path, (720, 1600))
            client, phone = socket.socketpair()
            requests = []
            def respond():
                with phone:
                    requests.append(exact(phone, 9))
                    data = json.dumps(dict(output_width=720, output_height=1600)).encode()
                    phone.sendall(struct.pack('>H', len(data)) + data)
            worker = threading.Thread(target=respond); worker.start()
            with client:
                details = desktop_settings(client, {}, path)
                self.assertEqual(details['output_height'], 1600)
                self.assertEqual(desktop_settings(client, details, path), details)
            worker.join(timeout=2)
            self.assertEqual(requests, [b'\x08' + struct.pack('>II', 720, 1600)])


if __name__ == '__main__': unittest.main()
