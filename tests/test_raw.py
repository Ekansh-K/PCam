from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RawTests(unittest.TestCase):
    def test_packed_raw_and_padded_planar_output(self):
        build = ROOT / 'scratch/test-raw'
        build.mkdir(parents=True, exist_ok=True)
        subprocess.run(['javac', '--release', '8', '-d', str(build),
                        str(ROOT / 'android/src/dev/pcam/app/RawDownsample.java'),
                        str(ROOT / 'tests/RawFixture.java')], check=True)
        subprocess.run(['java', '-cp', str(build), 'dev.pcam.app.RawFixture'], check=True)
        jdk = Path(subprocess.check_output(['readlink', '-f', '/usr/bin/javac'], text=True).strip()).parents[1]
        subprocess.run(['gcc', '-O3', '-fPIC', '-shared', '-Wall', '-Wextra', '-Werror',
                        '-I', str(jdk / 'include'), '-I', str(jdk / 'include/linux'),
                        str(ROOT / 'android/raw_downsample.c'), '-o', str(build / 'libpcam_raw.so')], check=True)
        subprocess.run(['java', '-Djava.library.path=' + str(build), '-cp', str(build), 'dev.pcam.app.RawFixture', 'native'], check=True)
