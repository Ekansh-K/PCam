"""Startup regressions from missing USB pairing and an unloaded webcam driver."""
import unittest
from unittest.mock import patch
from pcam import direct


class DirectStartupTests(unittest.TestCase):
    def test_connected_wifi_adapter_is_detected_without_a_machine_specific_name(self):
        with patch('pcam.direct.command', return_value='phy#0\n\tInterface wlan0\n\tInterface wlp2s0\n'), \
             patch('pcam.direct.router_link', side_effect=[RuntimeError('not connected'), ('ap', 5745)]):
            self.assertEqual(direct.connected_interface(), 'wlp2s0')

    def test_auto_select_connected_phone_and_explain_missing_usb(self):
        devices = 'List of devices attached\nb6d3efda device usb:3-2 model:CPH2613\n'
        self.assertEqual(direct.select_phone(devices), 'b6d3efda')
        self.assertEqual(direct.select_phone(devices, 'b6d3efda'), 'b6d3efda')
        for output, message in [('List of devices attached\n', 'Connect the phone'),
                                ('List of devices attached\nb6d3efda unauthorized\n', 'Allow USB debugging'),
                                (devices + 'another device usb:3-3\n', 'More than one')]:
            with self.subTest(output=output), self.assertRaisesRegex(RuntimeError, message):
                direct.select_phone(output)
        with self.assertRaisesRegex(RuntimeError, 'not connected'):
            direct.select_phone(devices, 'old-device')

    def test_missing_webcam_is_loaded_and_checked_as_desktop_user(self):
        checked = []
        with patch('pcam.direct.Path.exists', side_effect=[False, True]), patch('pcam.direct.command') as command:
            direct.prepare_webcam('/dev/video10', lambda *args: checked.append(args) or 'Driver name : v4l2 loopback')
        self.assertEqual(command.call_args_list[0].args,
                         ('modprobe', 'v4l2loopback', 'devices=1', 'video_nr=10', 'card_label=PCam', 'exclusive_caps=1'))
        self.assertEqual(checked, [('v4l2-ctl', '--device', '/dev/video10', '--info')])

    def test_child_failure_names_the_process_and_receiver_reason(self):
        with patch('pcam.direct.Path.read_text', return_value='Traceback\nRuntimeError: Cannot open virtual webcam\n'):
            reason = direct.failure_reason('Webcam receiver', 1, direct.ROOT / 'scratch/live-color.log')
        self.assertIn('Webcam receiver', reason)
        self.assertIn('Cannot open virtual webcam', reason)


if __name__ == '__main__': unittest.main()
