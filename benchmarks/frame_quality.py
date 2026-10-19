"""Detect uniform green corruption in a recorded camera stream or YUYV webcam capture."""
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np


def inspect(path, ffmpeg, raw_size=None, limit=240):
    command = [str(ffmpeg), '-v', 'error']
    if raw_size:
        command += ['-f', 'rawvideo', '-pixel_format', 'yuyv422', '-video_size', raw_size]
    command += ['-i', str(path), '-frames:v', str(limit), '-vf', 'scale=160:120', '-vsync', '0',
                '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, capture_output=True, check=True)
    frames = np.frombuffer(result.stdout, np.uint8).reshape(-1, 120, 160, 3)
    if not len(frames): raise ValueError('No decoded frames')
    f = frames.astype(np.int16)
    green = ((f[:,:,:,1] > 60) & (f[:,:,:,1] - f[:,:,:,0] > 60) &
             (f[:,:,:,1] - f[:,:,:,2] > 60)).mean(axis=(1,2))
    uniform = frames.std(axis=(1,2)).max(axis=1) < 4
    bad = np.flatnonzero((green > .99) & uniform).tolist()
    return dict(frames=len(frames), uniform_green_frames=len(bad), green_frame_indices=bad), frames


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--ffmpeg', type=Path, default=Path('scratch/tools/ffmpeg/usr/bin/ffmpeg'))
    p.add_argument('--raw-size', help='Widthxheight for YUYV input')
    p.add_argument('--limit', type=int, default=240)
    p.add_argument('--output', type=Path)
    a = p.parse_args(); result, frames = inspect(a.input, a.ffmpeg, a.raw_size, a.limit)
    if a.output: a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    raise SystemExit(1 if result['uniform_green_frames'] else 0)
