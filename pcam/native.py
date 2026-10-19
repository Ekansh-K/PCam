"""Bindings to the measured FFmpeg decoder and V4L2 output used by both prototypes."""
import ctypes as C
import subprocess
from .paths import ROOT, STATE, PACKAGED


def probe_path():
    return ROOT / 'lib/v4l2-probe' if PACKAGED else STATE / 'v4l2-probe'

class Frame(C.Structure):
    _fields_ = [("pts_us", C.c_int64), ("ready_ns", C.c_int64), ("width", C.c_int), ("height", C.c_int),
                ("converted_ns", C.c_int64), ("write_start_ns", C.c_int64),
                ("write_done_ns", C.c_int64), ("fingerprint", C.c_uint64)]

def build():
    output = ROOT / 'lib/pcam-decoder.so' if PACKAGED else STATE / 'pcam-decoder.so'
    if not PACKAGED:
        STATE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["g++", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    str(ROOT / "benchmarks/decoder.cpp"), "-I", str(ROOT / "scratch/tools/libyuv/usr/include"),
                    "-o", str(output), "-lavcodec", "-lavutil", "-lswscale", "-l:libyuv.so.0"], check=True)
        subprocess.run(["g++", "-O2", "-Wall", "-Wextra", "-Werror", str(ROOT / "benchmarks/v4l2_probe.cpp"),
                    "-o", str(probe_path())], check=True)
    library = C.CDLL(str(output))
    library.decoder_open.argtypes = [C.c_char_p]; library.decoder_open.restype = C.c_void_p
    library.decoder_feed.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_int64, C.POINTER(Frame), C.c_int]
    library.decoder_feed_gray.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_int64, C.c_int, C.c_int, C.POINTER(Frame)]
    library.decoder_set_rotation.argtypes = [C.c_void_p, C.c_int]
    library.decoder_set_size.argtypes = [C.c_void_p, C.c_int, C.c_int]
    library.decoder_set_output.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    library.decoder_close.argtypes = [C.c_void_p]
    library.decoder_output_pixels.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
    library.decoder_output_pixels.restype = C.c_void_p
    return library
