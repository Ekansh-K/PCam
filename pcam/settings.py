"""The two supported webcam presets, with portrait or landscape orientation."""
import json
import os
from pathlib import Path

PRESETS = ((480, 960), (720, 1600))


def validate_size(size):
    if tuple(size) not in PRESETS + tuple((height, width) for width, height in PRESETS):
        raise ValueError('Unsupported webcam resolution')
    return tuple(size)


def read_size(path):
    values = json.loads(Path(path).read_text())
    size = (values['output_width'], values['output_height'])
    return validate_size(size)


def read_settings(path):
    values = json.loads(Path(path).read_text())
    size = validate_size((values['output_width'], values['output_height']))
    rotation = values.get('rotation', 90 if size[1] >= size[0] else 0)
    if rotation not in (0, 90): raise ValueError('Unsupported camera orientation')
    return size, rotation


def write_size(path, size, rotation=None):
    size = validate_size(size)
    path = Path(path); temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(dict(output_width=size[0], output_height=size[1],
                       rotation=rotation if rotation is not None else 90 if size[1] >= size[0] else 0), handle)
    os.replace(temporary, path)
