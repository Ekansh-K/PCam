from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class NativeOutputTests(unittest.TestCase):
    def test_actual_rotation_and_range_conversion(self):
        executable = ROOT / "scratch/test-native-output"
        subprocess.run(["g++", "-O2", "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/test_output.cpp"),
                        "-I", str(ROOT / "scratch/tools/libyuv/usr/include"), "-o", str(executable),
                        "-lavcodec", "-lavutil", "-lswscale", "-l:libyuv.so.0"], check=True)
        subprocess.run([str(executable)], check=True)

if __name__ == "__main__": unittest.main()
