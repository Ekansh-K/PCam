"""Read-only application files and writable per-user session data."""
import os
from pathlib import Path
import pwd
import shutil

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = (ROOT / 'lib/pcam-decoder.so').is_file()


def state_directory():
    if not PACKAGED:
        return ROOT / 'scratch'
    uid = int(os.environ.get('PKEXEC_UID') or os.environ.get('SUDO_UID') or os.getuid())
    return Path(pwd.getpwuid(uid).pw_dir) / '.cache/pcam'


STATE = state_directory()


def tool(name, development_path):
    local = ROOT / 'scratch/tools' / development_path
    return str(local) if local.is_file() else shutil.which(name) or name
