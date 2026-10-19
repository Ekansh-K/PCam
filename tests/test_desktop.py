"""Desktop failure recovery and the helper's private stop pipe."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import QApplication
from pcam.desktop import Window
from pcam.picker import ResolutionPicker


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_failed_helper_launch_leaves_camera_connectable(self):
        window = Window(); window.poll.stop()
        window.show_devices([dict(name='Test phone', serial='fixture', busy=False)], 'direct')
        window.connecting = True; window.set_busy(True)
        process = window.process = QProcess(window)
        process.errorOccurred.connect(window.backend_error)
        process.start('/nonexistent/pcam-test-helper')
        self.assertFalse(process.waitForStarted(1000))
        self.assertIsNone(window.process)
        self.assertTrue(window.connect_button.isEnabled())
        self.assertTrue(window.scan_button.isEnabled())
        self.assertIn('Could not start', window.detail.text())
        window.close(); window.deleteLater()

    def test_direct_uses_pairing_panel_and_shared_uses_devices(self):
        window = Window(); window.poll.stop()
        with patch.object(window,'scan'):
            window.set_mode('direct')
            self.assertIs(window.left_stack.currentWidget(),window.qr_panel)
            self.assertTrue(window.connect_button.isEnabled())
            window.set_mode('shared')
            self.assertIs(window.left_stack.currentWidget(),window.devices_panel)
        window.close(); window.deleteLater()

    def test_picker_only_exposes_two_presets_and_swaps_dimensions(self):
        picker = ResolutionPicker()
        self.assertEqual(len(picker.buttons),2)
        self.assertEqual(picker.output_size(),(480,960))
        picker.landscape_button.click()
        self.assertEqual(picker.output_size(),(960,480)); self.assertEqual(picker.rotation(),0)
        picker.buttons[1].click()
        self.assertEqual(picker.output_size(),(1600,720))
        picker.portrait_button.click()
        self.assertEqual(picker.output_size(),(720,1600)); self.assertEqual(picker.rotation(),90)
        picker.deleteLater()

    def test_pairing_panel_disappears_after_video_starts_and_returns_on_disconnect(self):
        window = Window(); window.poll.stop(); window.set_mode('direct')
        self.assertFalse(window.left_stack.isHidden())
        process = Mock(); process.readAllStandardOutput.return_value = b''
        window.process = process
        with tempfile.TemporaryDirectory() as folder, patch('pcam.desktop.STATE',Path(folder)):
            scratch = Path(folder)
            (scratch/'live-color.log').write_text('PCam feeds /dev/video10 at 480×960.\n116 FPS, 0 recovery events\n')
            window.read_status()
            self.assertEqual(window.status_label.text(),'CONNECTED')
            self.assertTrue(window.left_stack.isHidden())
            window.backend_finished(0,QProcess.NormalExit)
            self.assertFalse(window.left_stack.isHidden())
            self.assertEqual(window.connect_button.text(),'Show pairing QR')
        window.close(); window.deleteLater()

    def test_stop_message_and_parent_pipe_closure_stop_helper(self):
        code = '''import signal,sys,time
from pcam.session import desktop_stop
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
desktop_stop()
print('ready', flush=True)
while True: time.sleep(.05)
'''
        for request in (b'stop\n', b''):
            with self.subTest(request=request):
                child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    self.assertEqual(child.stdout.readline(), b'ready\n')
                    output, error = child.communicate(request, timeout=3)
                    self.assertEqual(child.returncode, 0, error.decode())
                finally:
                    if child.poll() is None: child.kill(); child.communicate()


if __name__ == '__main__': unittest.main()
