#!/usr/bin/env python3
"""Build the Ubuntu 22.04 amd64 installer without development tools or private data."""
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pcam import native


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def main():
    distribution = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if distribution.get('VERSION_ID', '').strip('"') != '22.04' or distribution.get('ID', '').strip('"') != 'ubuntu':
        raise SystemExit('Build this package on Ubuntu 22.04. Its FFmpeg dependencies are specific to that release.')
    if subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip() != 'amd64':
        raise SystemExit('This release targets amd64.')
    version = ET.parse(ROOT / 'android/AndroidManifest.xml').getroot().get('{http://schemas.android.com/apk/res/android}versionName')
    stage = ROOT / 'scratch/ubuntu-package'
    if stage.exists(): shutil.rmtree(stage)
    application = stage / 'usr/lib/pcam'
    application.mkdir(parents=True)
    shutil.copytree(ROOT / 'pcam', application / 'pcam', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assets = application / 'assets'
    assets.mkdir()
    for name in ('pcam.svg', 'PCam-NerdFont.ttf', 'NerdFont-OFL.txt'):
        shutil.copy2(ROOT / 'assets' / name, assets / name)
    for name in ('pcam-app', 'pcam-backend', 'start-pcam'):
        shutil.copy2(ROOT / name, application / name)
        (application / name).chmod(0o755)
    native.build()
    library = application / 'lib'
    library.mkdir()
    shutil.copy2(ROOT / 'scratch/pcam-decoder.so', library / 'pcam-decoder.so')
    shutil.copy2(ROOT / 'scratch/v4l2-probe', library / 'v4l2-probe')
    run('strip', '--strip-unneeded', library / 'pcam-decoder.so', library / 'v4l2-probe')
    binaries = stage / 'usr/bin'
    binaries.mkdir(parents=True)
    (binaries / 'pcam').symlink_to('../lib/pcam/pcam-app')
    (binaries / 'pcam-direct').symlink_to('../lib/pcam/start-pcam')
    desktop = stage / 'usr/share/applications'
    desktop.mkdir(parents=True)
    shutil.copy2(ROOT / 'PCam.desktop', desktop / 'pcam.desktop')
    icons = stage / 'usr/share/icons/hicolor/scalable/apps'
    icons.mkdir(parents=True)
    shutil.copy2(ROOT / 'assets/pcam.svg', icons / 'pcam.svg')
    documentation = stage / 'usr/share/doc/pcam'
    documentation.mkdir(parents=True)
    shutil.copy2(ROOT / 'README.md', documentation / 'README.md')
    shutil.copy2(ROOT / 'assets/NerdFont-OFL.txt', documentation / 'NerdFont-OFL.txt')
    rules = stage / 'usr/lib/udev/rules.d'
    rules.mkdir(parents=True)
    (rules / '70-pcam.rules').write_text('SUBSYSTEM=="video4linux", KERNEL=="video[0-9]*", ATTR{name}=="PCam", TAG+="uaccess"\n')
    control = stage / 'DEBIAN'
    control.mkdir()
    size = sum(path.stat().st_size for path in stage.rglob('*') if path.is_file()) // 1024
    (control / 'control').write_text(f'''Package: pcam
Version: {version}
Section: video
Priority: optional
Architecture: amd64
Maintainer: Ekansh-K <Ekansh-K@users.noreply.github.com>
Installed-Size: {size}
Depends: python3 (>= 3.10), python3-pyqt5, python3-numpy, python3-qrcode (>= 7.3), libavcodec58, libavutil56, libswscale5, libyuv0, libstdc++6, libc6 (>= 2.35), policykit-1, network-manager, iw, wpasupplicant, dnsmasq-base, v4l-utils, kmod, iproute2
Suggests: adb, linux-image-generic-hwe-22.04
Homepage: https://github.com/Ekansh-K/PCam
Description: Use your Android phone as a wireless Ubuntu webcam
 Minimal desktop controls for shared Wi-Fi and QR-paired Wi-Fi Direct.
 Requires a working v4l2loopback kernel module and a compatible Android phone.
 Built for Ubuntu 22.04 amd64.
''')
    (control / 'postinst').write_text('''#!/bin/sh
set -e
if [ "$1" = configure ]; then
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger --subsystem-match=video4linux || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
    fi
fi
''')
    (control / 'postinst').chmod(0o755)
    for folder in [stage, *[path for path in stage.rglob('*') if path.is_dir()]]:
        folder.chmod(0o755)
    output = ROOT / 'dist' / f'PCam-{version}-ubuntu22.04-amd64.deb'
    output.parent.mkdir(exist_ok=True)
    run('dpkg-deb', '--root-owner-group', '--build', stage, output)
    print(output)


if __name__ == '__main__': main()
