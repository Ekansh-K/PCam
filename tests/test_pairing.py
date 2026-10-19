"""The desktop QR is decoded by the same ZXing engine as the phone camera."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import cv2
from pcam.pairing import payload, qr_pixels
from pcam.run import ROOT


class PairingTests(unittest.TestCase):
    def test_camera_decoder_reads_desktop_qr_in_both_orientations(self):
        details = payload('DIRECT-00-PCam','test-password','02:00:00:00:00:01',5745,'172.29.67.1','0'*32)
        image = qr_pixels(details)
        with tempfile.TemporaryDirectory(dir=ROOT/'scratch') as folder:
            jar = ROOT/'scratch/tools/zxing-core.jar'
            subprocess.run(['javac','-cp',str(jar),'-d',folder,str(ROOT/'tests/QrRoundtrip.java')],check=True)
            for rotated in (image, cv2.rotate(image,cv2.ROTATE_90_CLOCKWISE)):
                path = Path(folder)/'qr.png'; cv2.imwrite(str(path),cv2.resize(rotated,None,fx=4,fy=4,interpolation=cv2.INTER_NEAREST))
                output = subprocess.check_output(['java','-cp',folder+':'+str(jar),'QrRoundtrip',str(path)],text=True)
                self.assertEqual(json.loads(output),details)


if __name__ == '__main__': unittest.main()
